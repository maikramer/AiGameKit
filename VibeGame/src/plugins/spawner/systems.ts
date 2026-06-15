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
import type { SpawnGroupSpec, SpawnTemplateSpec } from './types';
import { TransformHierarchySystem } from '../transforms';
import { WorldTransform } from '../transforms/components';
import { getGltfLocalAABB } from '../gltf-xml/gltf-bounds-cache';
import {
  SpawnExclusion,
  isSpawnAreaFree,
  registerSpawnFootprint,
} from './occupancy';

const spawnerQuery = defineQuery([SpawnerPending]);
const terrainSpawnedQuery = defineQuery([TerrainSpawned]);
const exclusionQuery = defineQuery([SpawnExclusion]);

/**
 * Per-instance footprint radius (before scale): explicit `footprint-radius`,
 * else half the GLB footprint, else a small prop default.
 */
function footprintBaseRadius(spec: SpawnGroupSpec, urls: string[]): number {
  if (spec.footprintRadius > 0) return spec.footprintRadius;
  let best = 0;
  for (const url of urls) {
    const aabb = getGltfLocalAABB(url);
    if (!aabb) continue;
    const half = Math.max(aabb.maxX - aabb.minX, aabb.maxZ - aabb.minZ) / 2;
    if (half > best) best = half;
  }
  return best > 0 ? best : 0.8;
}

function templateUrls(spec: SpawnGroupSpec): string[] {
  const urls: string[] = [];
  for (const tpl of spec.templates) {
    const u = tpl.attributes.url;
    if (typeof u === 'string' && u.trim()) urls.push(u.trim());
  }
  return urls;
}

let callbackRegistered = false;

/** Frames a spawn group may wait for an async heightmap before giving up and
 * placing on whatever (possibly flat) sampler exists. ~10s at 60fps — long
 * enough for a slow heightmap decode, short enough to not hang forever if the
 * heightmap genuinely fails to load. */
const MAX_SPAWN_HEIGHTMAP_DEFER_FRAMES = 600;
let _spawnHeightmapDeferFrames = 0;

/**
 * A terrain declares a `heightmapUrl` but its sampler has no data yet — the
 * heightmap is still decoding. Spawning now would place entities on the flat
 * placeholder surface (y≈0) and leave them buried once the real terrain rises.
 */
function isTerrainHeightmapPending(state: State): boolean {
  const tctx = getTerrainContext(state);
  for (const [, data] of tctx) {
    if (data.heightmapUrl && data.sampler.data === null) return true;
  }
  return false;
}
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

    // Explicit no-spawn zones go into the occupancy registry before any group
    // samples positions this frame.
    for (const e of exclusionQuery(state.world)) {
      if (SpawnExclusion.registered[e]) continue;
      SpawnExclusion.registered[e] = 1;
      registerSpawnFootprint(
        state,
        SpawnExclusion.x[e],
        SpawnExclusion.z[e],
        SpawnExclusion.radius[e]
      );
    }

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

    // Defer spawning until the terrain heightmap has decoded, otherwise entities
    // get placed on the flat placeholder and end up buried when terrain rises.
    if (isTerrainHeightmapPending(state)) {
      if (_spawnHeightmapDeferFrames < MAX_SPAWN_HEIGHTMAP_DEFER_FRAMES) {
        _spawnHeightmapDeferFrames++;
        return;
      }
      // Fallback: heightmap is taking too long (or failed) — spawn anyway.
    }

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


      const templateRadiusBase = footprintBaseRadius(spec, templateUrls(spec));

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
          s = cand;
          if (!isNormalWithinSlopeLimit(cand.normal, maxSlope)) continue;
          if (
            spec.avoidOverlaps &&
            !isSpawnAreaFree(state, wx, wz, templateRadiusBase * spec.scaleMax)
          ) {
            continue;
          }
          foundValidSlope = true;
          break;
        }

        if (!s) continue;
        if (!foundValidSlope && !acceptAnySlope) {
          continue;
        }
        if (
          !foundValidSlope &&
          spec.avoidOverlaps &&
          !isSpawnAreaFree(state, wx, wz, templateRadiusBase * spec.scaleMax)
        ) {
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

        if (spec.avoidOverlaps) {
          // Conservative: the per-instance scale is drawn inside the spawn.
          registerSpawnFootprint(
            state,
            wx,
            wz,
            templateRadiusBase * spec.scaleMax
          );
        }
        spawnOne(state, spec, rand, wx, wy, wz, template);
      }

      SpawnerPending.spawned[eid] = 1;
    }
  },
};
