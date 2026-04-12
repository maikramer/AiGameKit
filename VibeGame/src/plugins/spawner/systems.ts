import { defineQuery, type State, type System } from '../../core';
import { SpawnerPending } from './components';
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
import { Transform, WorldTransform } from '../transforms/components';

const spawnerQuery = defineQuery([SpawnerPending]);

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

export const TerrainSpawnSystem: System = {
  group: 'simulation',
  after: [TransformHierarchySystem],
  update(state) {
    if (state.headless) return;

    const specs = getSpawnGroupSpecs(state);
    if (specs.size === 0) return;

    for (const eid of spawnerQuery(state.world)) {
      if (SpawnerPending.spawned[eid]) continue;

      const spec = specs.get(eid);
      if (!spec) {
        SpawnerPending.spawned[eid] = 1;
        continue;
      }

      const surfaceProbe = sampleTerrainSurface(
        state,
        0,
        0,
        spec.surfaceEpsilon
      );
      if (!surfaceProbe) continue;

      const rand = mulberry32(spec.seed >>> 0);
      const [ax, , az] = anchorOffset(state, eid);
      const minX = spec.regionMin[0] + ax;
      const maxX = spec.regionMax[0] + ax;
      const minZ = spec.regionMin[2] + az;
      const maxZ = spec.regionMax[2] + az;

      const maxSlope = Number.isFinite(spec.maxSlopeDeg)
        ? spec.maxSlopeDeg
        : 45;
      const acceptAnySlope = maxSlope >= 90 - 1e-6;

      for (let i = 0; i < spec.count; i++) {
        let wx = minX;
        let wz = minZ;
        let s: TerrainSurfaceSample | null = null;
        let foundValidSlope = false;
        const attempts = Math.max(1, spec.maxSlopePlacementAttempts);
        for (let attempt = 0; attempt < attempts; attempt++) {
          wx = minX + rand() * (maxX - minX);
          wz = minZ + rand() * (maxZ - minZ);
          const cand = sampleTerrainSurface(state, wx, wz, spec.surfaceEpsilon);
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
