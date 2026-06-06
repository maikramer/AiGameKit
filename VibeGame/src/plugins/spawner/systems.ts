import { defineQuery, type State, type System } from '../../core';
import { getTerrainContext, registerHeightmapReloadCallback } from '../terrain';
import { Transform } from '../transforms/components';
import { PlacePending, SpawnerPending, TerrainSpawned } from './components';
import { getSpawnGroupSpecs } from './context';
import { spawnTemplateAtTerrain } from './spawn-template';
import {
  isNormalWithinSlopeLimit,
  sampleTerrainSurface,
  sampleTerrainSurfaceMatrix,
  sinkOffsetForSlope,
  partialAlignEuler,
  type TerrainSurfaceSample,
} from './surface';
import type { SpawnGroupSpec, SpawnTemplateSpec } from './types';
import { TransformHierarchySystem } from '../transforms';
import { WorldTransform } from '../transforms/components';
import { VegetationInstancer } from '../vegetation/systems';
import { MeshInstanceSystem } from '../rendering/systems';
import { getGltfLocalAABB } from '../gltf-xml/gltf-bounds-cache';
import {
  BodyType,
  Collider,
  ColliderShape,
  Rigidbody,
} from '../physics/components';
import { syncBodyQuaternionFromEuler } from '../physics/utils';

const spawnerQuery = defineQuery([SpawnerPending]);
const terrainSpawnedQuery = defineQuery([TerrainSpawned]);

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
const vegetationInstancers = new Map<number, VegetationInstancer>();

function resolveYaw(rand: () => number, spec: SpawnGroupSpec): number {
  if (!spec.randomYaw) return 0;
  if (spec.yawDistribution === 'discrete' && spec.yawDiscreteDeg.length > 0) {
    const idx = Math.floor(rand() * spec.yawDiscreteDeg.length);
    return (spec.yawDiscreteDeg[idx]! * Math.PI) / 180;
  }
  return rand() * Math.PI * 2;
}

function resolveScale(rand: () => number, spec: SpawnGroupSpec): number {
  if (
    spec.scaleDistribution === 'discrete' &&
    spec.scaleDiscreteValues.length > 0
  ) {
    const idx = Math.floor(rand() * spec.scaleDiscreteValues.length);
    return spec.scaleDiscreteValues[idx]!;
  }
  return spec.scaleMin + rand() * (spec.scaleMax - spec.scaleMin);
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

      // === INSTANCED VEGETATION HOOK ===
      const firstTemplate = spec.templates[0];
      if (firstTemplate && spec.instanced) {
        const vegUrl = String(firstTemplate.attributes['url'] || '');
        if (!vegUrl) {
          console.warn(
            '[spawner] Instanced vegetation group skipped: no url attribute'
          );
          SpawnerPending.spawned[eid] = 1;
          continue;
        }

        const vegInstancer = new VegetationInstancer();
        vegetationInstancers.set(eid, vegInstancer);

        vegInstancer
          .initializeFromSpec(
            {
              url: vegUrl,
              lod1Url: firstTemplate.attributes['lod1-url']
                ? String(firstTemplate.attributes['lod1-url'])
                : undefined,
              lod2Url: firstTemplate.attributes['lod2-url']
                ? String(firstTemplate.attributes['lod2-url'])
                : undefined,
              role: firstTemplate.role || 'static',
              profile: firstTemplate.childProfile,
            },
            state
          )
          .then(() => {
            const positions: Array<{
              x: number;
              y: number;
              z: number;
              scale: number;
              yaw: number;
              alignEuler: [number, number, number];
            }> = [];
            const aabb = getGltfLocalAABB(vegUrl);
            const halfWidth = aabb
              ? Math.max(aabb.maxX - aabb.minX, aabb.maxZ - aabb.minZ) / 2
              : 0.5;

            for (let i = 0; i < instanceCount; i++) {
              let wx = minX;
              let wz = minZ;
              let s: (TerrainSurfaceSample & { slopeAngleRad: number }) | null =
                null;
              let foundValidSlope = false;
              const attempts = Math.max(1, spec.maxSlopePlacementAttempts);
              for (let attempt = 0; attempt < attempts; attempt++) {
                wx = minX + rand() * (maxX - minX);
                wz = minZ + rand() * (maxZ - minZ);
                const cand = sampleTerrainSurfaceMatrix(
                  state,
                  wx,
                  wz,
                  spec.surfaceEpsilon,
                  spec.surfaceEpsilonAuto
                );
                if (!cand) continue;
                s = cand;
                if (isNormalWithinSlopeLimit(cand.normal, maxSlope)) {
                  foundValidSlope = true;
                  break;
                }
              }
              if (!s) continue;
              if (!foundValidSlope && !acceptAnySlope) continue;
              const scale = resolveScale(rand, spec);
              const sink = sinkOffsetForSlope(
                s.slopeAngleRad,
                halfWidth * scale
              );
              const yaw = resolveYaw(rand, spec);
              // partialAlignEuler bakes the yaw in (about the trunk). When not
              // aligning to terrain, keep the yaw as a plain +Y rotation so it
              // isn't silently dropped by addInstance.
              const alignEuler: [number, number, number] = spec.alignToTerrain
                ? partialAlignEuler(s.normal, yaw, s.slopeAngleRad)
                : [0, yaw, 0];
              positions.push({
                x: wx,
                y: s.worldY - sink,
                z: wz,
                scale,
                yaw,
                alignEuler,
              });
            }

            for (const p of positions) {
              vegInstancer.addInstance(
                p.x,
                p.y,
                p.z,
                p.scale,
                p.yaw,
                p.alignEuler
              );
            }
            vegInstancer.markReady(state);

            if (aabb && firstTemplate.attributes['body-type'] !== undefined) {
              for (const p of positions) {
                const physEid = state.createEntity();
                state.addComponent(physEid, Transform);
                state.addComponent(physEid, Rigidbody);
                state.addComponent(physEid, Collider);

                const sizeX = (aabb.maxX - aabb.minX) * p.scale;
                const sizeY = (aabb.maxY - aabb.minY) * p.scale;
                const sizeZ = (aabb.maxZ - aabb.minZ) * p.scale;
                const centerX = ((aabb.minX + aabb.maxX) / 2) * p.scale;
                const centerY = ((aabb.minY + aabb.maxY) / 2) * p.scale;
                const centerZ = ((aabb.minZ + aabb.maxZ) / 2) * p.scale;

                Transform.posX[physEid] = p.x;
                Transform.posY[physEid] = p.y;
                Transform.posZ[physEid] = p.z;
                Transform.scaleX[physEid] = 1;
                Transform.scaleY[physEid] = 1;
                Transform.scaleZ[physEid] = 1;
                Transform.dirty[physEid] = 1;

                Rigidbody.type[physEid] = BodyType.Fixed;
                Rigidbody.posX[physEid] = p.x;
                Rigidbody.posY[physEid] = p.y;
                Rigidbody.posZ[physEid] = p.z;
                Rigidbody.eulerY[physEid] = p.yaw;
                Rigidbody.gravityScale[physEid] = 1;
                syncBodyQuaternionFromEuler(physEid);

                Collider.shape[physEid] = ColliderShape.Box;
                Collider.sizeX[physEid] = sizeX;
                Collider.sizeY[physEid] = sizeY;
                Collider.sizeZ[physEid] = sizeZ;
                Collider.posOffsetX[physEid] = centerX;
                Collider.posOffsetY[physEid] = centerY;
                Collider.posOffsetZ[physEid] = centerZ;
                Collider.friction[physEid] = 0.5;
                Collider.restitution[physEid] = 0;
                Collider.density[physEid] = 1;
                Collider.isSensor[physEid] = 0;
                Collider.membershipGroups[physEid] = 0xffff;
                Collider.filterGroups[physEid] = 0xffff;
                Collider.rotOffsetW[physEid] = 1;
              }
            }
          });

        SpawnerPending.spawned[eid] = 1;
        continue;
      }
      // === END INSTANCED HOOK ===

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

export const vegetationInstancerMap = vegetationInstancers;

export const VegetationUpdateSystem: System = {
  group: 'draw',
  after: [MeshInstanceSystem],
  update(state: State) {
    if (state.headless) return;
    for (const [, instancer] of vegetationInstancers) {
      instancer.update(state);
    }
  },
};
