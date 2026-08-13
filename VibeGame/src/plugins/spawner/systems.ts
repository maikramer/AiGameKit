import { logger } from '../../core/utils/logger';
import { defineSystem, defineQuery, type State, type System } from '../../core';
import {
  getTerrainContext,
  registerHeightmapReloadCallback,
  registerGroundMutationCallback,
} from '../terrain';
import { Transform } from '../transforms/components';
import { PlacePending, SpawnerPending, TerrainSpawned } from './components';
import { getSpawnGroupSpecs } from './context';
import { spawnTemplateAtTerrain } from './spawn-template';
import {
  isNormalWithinSlopeLimit,
  placementDeferDecision,
  sampleTerrainSurface,
  type TerrainSurfaceSample,
} from './surface';
import type { SpawnGroupSpec, SpawnTemplateSpec } from './types';
import { WorldTransform } from '../transforms/components';
import {
  flushGltfBoundsPrefetch,
  getGltfLocalAABB,
  getGltfLocalYBounds,
} from '../gltf-xml/gltf-bounds-cache';
import { getAabbPendingUrls } from './bounds-context';
import {
  isPointNearWater,
  isPointOnWaterBank,
  waterBodyAt,
} from '../water/registry';
import { crownHitsFlyingDeck, isPointOnRoad } from '../terrain/brush-registry';
import {
  SpawnExclusion,
  isSpawnAreaFree,
  registerSpawnFootprint,
} from './occupancy';
import { LakeApplySystem, RiverApplySystem } from '../water/systems';
import { RoadApplySystem } from '../road/systems';
import { TerrainPadApplySystem } from '../terrain/pad-systems';
import { templateVisualUrl } from './template-url';
import { applyTerrainSpawnedY } from './terrain-spawned-y';

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
    const u = templateVisualUrl(tpl);
    if (u) urls.push(u);
  }
  return urls;
}

/** GameAssets collision precompute is often the stump (~0.6 m), not the crown. */
const TREE_URL_RE = /(?:^|\/)(?:tree_|pine_)/i;
const STUMP_AABB_MAX_Y = 2;
const TREE_CROWN_FALLBACK_M = 10;

/**
 * Conservative crown height (m) for viaduct clearance. Prefers the visual
 * AABB; a stump-sized box on a tree URL is treated as ~10 m.
 */
function estimatedCrownHeight(spec: SpawnGroupSpec, urls: string[]): number {
  let maxH = 0;
  for (const url of urls) {
    const aabb = getGltfLocalAABB(url);
    let h = aabb ? aabb.maxY - Math.min(0, aabb.minY) : 0;
    if (h < STUMP_AABB_MAX_Y && TREE_URL_RE.test(url)) {
      h = TREE_CROWN_FALLBACK_M;
    }
    if (h > maxH) maxH = h;
  }
  if (maxH <= 0 && urls.some((u) => TREE_URL_RE.test(u))) {
    maxH = TREE_CROWN_FALLBACK_M;
  }
  return maxH * spec.scaleMax;
}

const spawnerStateMap = new WeakMap<State, SpawnerState>();

interface SpawnerState {
  callbackRegistered: boolean;
  spawnHeightmapDeferFrames: number;
}

function getSpawnerState(state: State): SpawnerState {
  let s = spawnerStateMap.get(state);
  if (!s) {
    s = { callbackRegistered: false, spawnHeightmapDeferFrames: 0 };
    spawnerStateMap.set(state, s);
  }
  return s;
}

/** Frames a spawn group may wait for an async heightmap before giving up and
 * placing on whatever (possibly flat) sampler exists. ~10s at 60fps — long
 * enough for a slow heightmap decode, short enough to not hang forever if the
 * heightmap genuinely fails to load. Flatten/carve never uses this budget. */
const MAX_SPAWN_HEIGHTMAP_DEFER_FRAMES = 600;
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

/**
 * Soft ceiling for density-mode foliage carpets. Above this, ECS entity
 * creation + terrain probes freeze the boot loading gate for many seconds.
 * Trees/rocks use `fixed` counts and are unaffected.
 */
const MAX_DENSITY_SPAWN_INSTANCES = 12_000;

/** Instâncias a colocar: fixo, densidade (obj/km² × área XZ em km²), ou inteiro uniforme no intervalo. */
function resolveSpawnInstanceCount(
  spec: SpawnGroupSpec,
  rand: () => number,
  areaKm2: number
): number {
  let n: number;
  switch (spec.spawnCountMode) {
    case 'fixed':
      n = Math.max(0, Math.floor(spec.count));
      break;
    case 'density':
      n = Math.max(0, Math.round(spec.densityPerKm2 * areaKm2));
      break;
    case 'random-range': {
      const lo = Math.min(spec.countRangeMin, spec.countRangeMax);
      const hi = Math.max(spec.countRangeMin, spec.countRangeMax);
      n = lo + Math.floor(rand() * (hi - lo + 1));
      break;
    }
    default:
      n = Math.max(0, Math.floor(spec.count));
      break;
  }
  if (
    spec.spawnCountMode === 'density' &&
    spec.spawnGroupProfile === 'foliage' &&
    n > MAX_DENSITY_SPAWN_INSTANCES
  ) {
    logger.warn(
      `[spawner] foliage density count ${n} capped to ${MAX_DENSITY_SPAWN_INSTANCES} ` +
        `(densityPerKm2=${spec.densityPerKm2} areaKm2=${areaKm2.toFixed(4)})`
    );
    return MAX_DENSITY_SPAWN_INSTANCES;
  }
  return n;
}

function resyncTerrainSpawnedHeights(state: State): void {
  for (const eid of terrainSpawnedQuery(state.world)) {
    applyTerrainSpawnedY(state, eid);
  }
}

export const TerrainSpawnSystem: System = defineSystem({
  name: 'TerrainSpawnSystem', // Same 'setup' bucket as pads/water/roads, strictly AFTER they stamp — so the
  // first spawn batch samples post-carve heights in the same frame the
  // heightmap lands (simulation would also work via the pending gate, but a
  // late Road flatten used to slip past the gate and bury/float trees).
  group: 'setup',
  after: [
    TerrainPadApplySystem,
    LakeApplySystem,
    RiverApplySystem,
    RoadApplySystem,
  ],
  update(state) {
    if (state.headless) return;

    // Kick queued AABB prefetches once KTX2/renderer exist (XML parse queues
    // earlier, before detectSupport can run).
    flushGltfBoundsPrefetch(state);

    const spawnerState = getSpawnerState(state);

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

    if (!spawnerState.callbackRegistered) {
      spawnerState.callbackRegistered = true;
      registerHeightmapReloadCallback(state, () => {
        resyncTerrainSpawnedHeights(state);
      });
      // Pads / lakes / rivers / road flatten also mutate the sampler after
      // some props may already be planted — snap them onto the new surface.
      registerGroundMutationCallback(state, () => {
        resyncTerrainSpawnedHeights(state);
      });
    }

    const specs = getSpawnGroupSpecs(state);
    if (specs.size === 0) return;

    // Defer until the heightmap has decoded and pads/water/road carves have
    // stamped. Carve never times out — spawning on the pre-carve sampler
    // leaves trees floating once the road digs. A missing heightmap may.
    if (
      placementDeferDecision(
        state,
        spawnerState.spawnHeightmapDeferFrames,
        MAX_SPAWN_HEIGHTMAP_DEFER_FRAMES
      ) === 'wait'
    ) {
      spawnerState.spawnHeightmapDeferFrames++;
      return;
    } else if (spawnerState.spawnHeightmapDeferFrames > 0) {
      // Terrain ready: reset so a later heightmap reload gets fresh defer protection.
      spawnerState.spawnHeightmapDeferFrames = 0;
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
        logger.warn(
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

      const urls = templateUrls(spec);
      const templateRadiusBase = footprintBaseRadius(spec, urls);
      const crownH = spec.avoidRoad ? estimatedCrownHeight(spec, urls) : 0;

      // Cluster centres (optional): dense patches instead of a uniform carpet.
      // Precomputed hubs (e.g. vegetation flower layer sharing grass hubs) win.
      const clusterCenters: Array<[number, number]> = [];
      if (spec.clusterCenters && spec.clusterCenters.length > 0) {
        for (const hub of spec.clusterCenters) {
          clusterCenters.push([hub[0], hub[1]]);
        }
      } else if (spec.clusterCount > 0 && spec.clusterRadius > 0) {
        const cAttempts = Math.max(spec.clusterCount * 8, 32);
        for (
          let c = 0;
          c < cAttempts && clusterCenters.length < spec.clusterCount;
          c++
        ) {
          const cx0 = minX + rand() * (maxX - minX);
          const cz0 = minZ + rand() * (maxZ - minZ);
          // Keep cluster hubs out of exclusions / earlier props.
          if (!isSpawnAreaFree(state, cx0, cz0, 0.5)) continue;
          if (spec.avoidWater && isPointNearWater(state, cx0, cz0)) continue;
          if (spec.avoidRoad && isPointOnRoad(state, cx0, cz0)) continue;
          if (spec.nearWater && !isPointOnWaterBank(state, cx0, cz0)) continue;
          clusterCenters.push([cx0, cz0]);
        }
      }

      for (let i = 0; i < instanceCount; i++) {
        let wx = minX;
        let wz = minZ;
        let s: TerrainSurfaceSample | null = null;
        let foundValidSlope = false;
        let waterSurfaceY: number | null = null;
        const attempts = Math.max(1, spec.maxSlopePlacementAttempts);
        for (let attempt = 0; attempt < attempts; attempt++) {
          if (clusterCenters.length > 0) {
            const hub =
              clusterCenters[Math.floor(rand() * clusterCenters.length)]!;
            // Uniform disc around hub (sqrt for area-uniform).
            const ang = rand() * Math.PI * 2;
            const rad = Math.sqrt(rand()) * spec.clusterRadius;
            wx = hub[0] + Math.cos(ang) * rad;
            wz = hub[1] + Math.sin(ang) * rad;
            // Soft clamp into region so edge clusters don't spill far out.
            wx = Math.min(maxX, Math.max(minX, wx));
            wz = Math.min(maxZ, Math.max(minZ, wz));
          } else {
            wx = minX + rand() * (maxX - minX);
            wz = minZ + rand() * (maxZ - minZ);
          }

          // `in-water`: the instance floats on a lake surface — accept only
          // points inside the waterline (the [shoreRadius, radius] ring is
          // beach floor, not water) and anchor Y to the surface instead of
          // the terrain sample. The organic shoreline (shapeRadius) can pull
          // the waterline in to 0.72× the nominal shore radius, so stay
          // inside that worst case or instances beach themselves.
          if (spec.inWater) {
            const body = waterBodyAt(state, wx, wz);
            if (!body) continue;
            // `in-water` floats instances on a lake surface (lilies, boats).
            // Rivers have no beach ring, so only lakes qualify here.
            if (body.kind !== 'lake') continue;
            const dx = wx - body.x;
            const dz = wz - body.z;
            if (Math.hypot(dx, dz) > body.shoreRadius * 0.72) continue;
            if (
              !isSpawnAreaFree(
                state,
                wx,
                wz,
                templateRadiusBase * spec.scaleMax * spec.scaleAxisMax
              )
            ) {
              continue;
            }
            waterSurfaceY = body.waterY;
            foundValidSlope = true;
            break;
          }

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
          // `near-water`: only the carved bank/beach ring (not the wet
          // surface). Rocks and reeds hug the shoreline without floating.
          if (spec.nearWater) {
            if (!isPointOnWaterBank(state, wx, wz)) continue;
          } else if (spec.avoidWater && isPointNearWater(state, wx, wz)) {
            // `avoid-water` excludes the FULL carve footprint (water + carved
            // banks/beach), not just the wet surface — otherwise props land on
            // the bank slope with their trunks leaning into the channel.
            continue;
          }
          if (spec.avoidRoad && isPointOnRoad(state, wx, wz)) {
            // Flatten-road corridor + plaza pad core — no trees/rocks on cobble.
            continue;
          }
          if (
            spec.avoidRoad &&
            crownH > 0 &&
            crownHitsFlyingDeck(state, wx, wz, cand.worldY + crownH)
          ) {
            // Valley forest is allowed under a viaduct; crowns through the
            // deck are not. Flying brushes are ignored by isPointOnRoad.
            continue;
          }
          // Always honour SpawnExclusion (+ footprints from earlier groups).
          // `avoidOverlaps` only controls whether THIS group registers discs.
          if (
            !isSpawnAreaFree(
              state,
              wx,
              wz,
              templateRadiusBase * spec.scaleMax * spec.scaleAxisMax
            )
          ) {
            continue;
          }
          foundValidSlope = true;
          break;
        }

        if (spec.inWater) {
          // Water placement either succeeded exactly or found no wet spot in
          // the attempt budget — there is no terrain fallback to relax to.
          if (waterSurfaceY === null) continue;
        } else {
          if (!s) continue;
          if (!foundValidSlope && !acceptAnySlope) {
            continue;
          }
          if (
            !foundValidSlope &&
            !isSpawnAreaFree(
              state,
              wx,
              wz,
              templateRadiusBase * spec.scaleMax * spec.scaleAxisMax
            )
          ) {
            continue;
          }
        }

        // Slightly above the surface so the mesh never z-fights the water disc.
        const wy = waterSurfaceY !== null ? waterSurfaceY : s!.worldY;

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
            templateRadiusBase * spec.scaleMax * spec.scaleAxisMax
          );
        }
        spawnOne(state, spec, rand, wx, wy, wz, template);
      }

      SpawnerPending.spawned[eid] = 1;
    }
  },
});

/**
 * Applies the deferred AABB base lift for props spawned with
 * `ground-align="aabb"` before their GLB bounds had cached.
 *
 * `spawnTemplateAtTerrain` writes `aabbPending=1` + the template URL on the
 * entity when the bounds weren't ready. Each frame this system re-checks the
 * bounds cache: once present it rewrites `Transform.posY` to add the missing
 * `normalY * (-minY * scaleY)` lift (matching what the spawn would have done)
 * and clears the flag, so the prop stops floating without a scene reload.
 *
 * Only `Transform.posY` is touched — static props don't run a character
 * controller, and the initial `mirrorPoseToRigidbody` already placed any
 * Fixed body. Rewriting a sleeping body's Y here would desync it.
 */
export const TerrainSpawnBoundsCatchUpSystem: System = defineSystem({
  name: 'TerrainSpawnBoundsCatchUpSystem',
  group: 'simulation',
  // Spawn now runs in setup; catch-up still follows in simulation so GLB
  // bounds that arrive mid-frame get a Y lift before physics/draw.
  update(state) {
    if (state.headless) return;
    // Late renderer / KTX2: keep draining the prefetch queue while catch-up waits.
    flushGltfBoundsPrefetch(state);
    const urls = getAabbPendingUrls(state);
    if (urls.size === 0) return;

    for (const eid of terrainSpawnedQuery(state.world)) {
      if (TerrainSpawned.aabbPending[eid] !== 1) continue;
      const url = urls.get(eid);
      if (!url) {
        TerrainSpawned.aabbPending[eid] = 0;
        continue;
      }
      const b = getGltfLocalYBounds(url);
      if (!b) continue; // still in flight; try again next frame

      const scaleY = TerrainSpawned.scaleY[eid] || 1;
      const normalY = TerrainSpawned.normalY[eid] || 1;
      // Origin already at the soles (pipeline GLBs): lifting by a later
      // centered Three.js box floats the trunk. Skip near-zero minY.
      if (Math.abs(b.minY) < 0.15) {
        TerrainSpawned.aabbPending[eid] = 0;
        urls.delete(eid);
        continue;
      }
      const lift = normalY * (-b.minY * scaleY);
      // Lift is part of the foot plant — keep yOffset in sync so resync /
      // creature re-sample don't drop the AABB correction.
      Transform.posY[eid] += lift;
      TerrainSpawned.yOffset[eid] += lift;
      Transform.dirty[eid] = 1;

      TerrainSpawned.aabbPending[eid] = 0;
      urls.delete(eid);
    }

    // Drop any orphaned URL entries whose entity lost TerrainSpawned.
    if (urls.size > 0) {
      for (const key of urls.keys()) {
        if (!state.exists(key) || !state.hasComponent(key, TerrainSpawned)) {
          urls.delete(key);
        }
      }
    }
  },
});
