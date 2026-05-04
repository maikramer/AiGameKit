import { defineQuery, type State, type System } from '../../core';
import { getTerrainContext, registerHeightmapReloadCallback } from '../terrain';
import { Transform } from '../transforms/components';
import { PlacePending, SpawnerPending, TerrainSpawned } from './components';
import { getSpawnGroupSpecs } from './context';
import { spawnTemplateAtTerrain } from './spawn-template';
import {
  isNormalWithinSlopeLimit,
  sampleTerrainSurface,
  type TerrainSurfaceSample,
} from './surface';
import { isTerrainUnderwaterAt } from './water-spawn';
import type { SpawnGroupSpec, SpawnTemplateSpec } from './types';
import { TransformHierarchySystem } from '../transforms';
import { WorldTransform } from '../transforms/components';

const spawnerQuery = defineQuery([SpawnerPending]);
const terrainSpawnedQuery = defineQuery([TerrainSpawned]);

let callbackRegistered = false;

function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a += 0x6d2b79f5;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function anchorOffset(
  state: State,
  spawnerEid: number
): [number, number, number] {
  if (state.hasComponent(spawnerEid, WorldTransform)) {
    return [
      WorldTransform.posX[spawnerEid],
      WorldTransform.posY[spawnerEid],
      WorldTransform.posZ[spawnerEid],
    ];
  }
  return [
    Transform.posX[spawnerEid],
    Transform.posY[spawnerEid],
    Transform.posZ[spawnerEid],
  ];
}

function spawnOne(
  state: State,
  spec: SpawnGroupSpec,
  rand: () => number,
  wx: number,
  wy: number,
  wz: number,
  template: SpawnTemplateSpec
): void {
  spawnTemplateAtTerrain(state, spec, rand, wx, wy, wz, template);
}

/** Instâncias a colocar: fixo, densidade (obj/km² × área XZ em km²), ou inteiro uniforme no intervalo. */
function resolveSpawnInstanceCount(
  spec: SpawnGroupSpec,
  rand: () => number,
  areaKm2: number
): number {
  switch (spec.spawnCountMode) {
    case 'fixed':
      return Math.max(0, Math.floor(spec.count));
    case 'density':
      return Math.max(0, Math.round(spec.densityPerKm2 * areaKm2));
    case 'random-range': {
      const lo = Math.min(spec.countRangeMin, spec.countRangeMax);
      const hi = Math.max(spec.countRangeMin, spec.countRangeMax);
      return lo + Math.floor(rand() * (hi - lo + 1));
    }
    default:
      return Math.max(0, Math.floor(spec.count));
  }
}

export const TerrainSpawnSystem: System = {
  group: 'simulation',
  after: [TransformHierarchySystem],
  update(state) {
    if (state.headless) return;

    if (!callbackRegistered) {
      callbackRegistered = true;
      registerHeightmapReloadCallback(state, () => {
        for (const eid of terrainSpawnedQuery(state.world)) {
          const x = state.hasComponent(eid, WorldTransform)
            ? WorldTransform.posX[eid]
            : Transform.posX[eid];
          const z = state.hasComponent(eid, WorldTransform)
            ? WorldTransform.posZ[eid]
            : Transform.posZ[eid];
          const eps = TerrainSpawned.surfaceEpsilon[eid] || 0.75;
          const s = sampleTerrainSurface(state, x, z, eps);
          if (s) {
            Transform.posY[eid] = s.worldY + TerrainSpawned.yOffset[eid];
            Transform.dirty[eid] = 1;
          }
        }
      });
    }

    const specs = getSpawnGroupSpecs(state);
    if (specs.size === 0) return;

    for (const eid of spawnerQuery(state.world)) {
      if (SpawnerPending.spawned[eid]) continue;

      const spec = specs.get(eid);
      if (!spec) {
        SpawnerPending.spawned[eid] = 1;
        continue;
      }

      const rand = mulberry32(spec.seed >>> 0);
      const [ax, , az] = anchorOffset(state, eid);
      const minX = spec.regionMin[0] + ax;
      const maxX = spec.regionMax[0] + ax;
      const minZ = spec.regionMin[2] + az;
      const maxZ = spec.regionMax[2] + az;

      const cx = (minX + maxX) / 2;
      const cz = (minZ + maxZ) / 2;

      // Multi-point probe: center + 4 corners. The center alone may map
      // to a water/invalid heightmap pixel (common when region is symmetric
      // around origin and terrain heightmap hasn't loaded yet).
      const probes: [number, number][] = [
        [cx, cz],
        [minX, minZ],
        [minX, maxZ],
        [maxX, minZ],
        [maxX, maxZ],
      ];
      let regionProbe: TerrainSurfaceSample | null = null;
      for (const [px, pz] of probes) {
        regionProbe = sampleTerrainSurface(
          state,
          px,
          pz,
          spec.surfaceEpsilon,
          spec.surfaceEpsilonAuto
        );
        if (regionProbe) break;
      }
      if (!regionProbe) {
        // If no terrain context is initialized at all, defer this frame
        // without marking the group as permanently done. Retry next frame.
        const terrainCtx = getTerrainContext(state);
        let terrainReady = false;
        for (const [, data] of terrainCtx) {
          if (data.initialized) {
            terrainReady = true;
            break;
          }
        }
        if (!terrainReady) continue;
        console.warn(
          `[spawner] SpawnGroup "group-${eid}" skipped: no terrain surface in region (${minX.toFixed(0)}..${maxX.toFixed(0)}, ${minZ.toFixed(0)}..${maxZ.toFixed(0)})`
        );
        PlacePending.spawned[eid] = 1;
        continue;
      }

      const width = Math.abs(maxX - minX);
      const depth = Math.abs(maxZ - minZ);
      const areaKm2 = (width * depth) / 1_000_000;
      const instanceCount = resolveSpawnInstanceCount(spec, rand, areaKm2);

      const maxSlope = Number.isFinite(spec.maxSlopeDeg)
        ? spec.maxSlopeDeg
        : 45;
      const acceptAnySlope = maxSlope >= 90 - 1e-6;

      for (let i = 0; i < instanceCount; i++) {
        let wx = minX;
        let wz = minZ;
        let s: TerrainSurfaceSample | null = null;
        let foundValidSlope = false;
        const attempts = Math.max(1, spec.maxSlopePlacementAttempts);
        for (let attempt = 0; attempt < attempts; attempt++) {
          wx = minX + rand() * (maxX - minX);
          wz = minZ + rand() * (maxZ - minZ);
          const cand = sampleTerrainSurface(
            state,
            wx,
            wz,
            spec.surfaceEpsilon,
            spec.surfaceEpsilonAuto
          );
          if (!cand) continue;
          if (
            spec.avoidWater &&
            isTerrainUnderwaterAt(state, wx, wz, cand.worldY)
          ) {
            continue;
          }
          s = cand;
          if (isNormalWithinSlopeLimit(cand.normal, maxSlope)) {
            foundValidSlope = true;
            break;
          }
        }

        if (!s) continue;
        if (!foundValidSlope && !acceptAnySlope) {
          continue;
        }

        const wy = s.worldY;

        let template: SpawnTemplateSpec;
        if (spec.pickStrategy === 'round-robin') {
          template = spec.templates[i % spec.templates.length]!;
        } else {
          template =
            spec.templates[Math.floor(rand() * spec.templates.length)]!;
        }

        spawnOne(state, spec, rand, wx, wy, wz, template);
      }

      SpawnerPending.spawned[eid] = 1;
    }
  },
};
