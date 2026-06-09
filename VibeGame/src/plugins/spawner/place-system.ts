import * as THREE from 'three';
import { eulerToQuaternion } from '../../core/math';
import { defineQuery, type State, type System } from '../../core';
import { getTerrainContext } from '../terrain';
import { PlacePending } from './components';
import { TerrainSpawned } from './components';
import type { GroupSpawnDefaults } from './profiles';
import { getPlacementSpecs } from './place-context';
import { spawnTemplateAtTerrain } from './spawn-template';
import { isNormalWithinSlopeLimit, sampleTerrainSurface } from './surface';
import { composeSpawnRotation } from './transform-merge';
import { Transform, WorldTransform } from '../transforms/components';
import { Rigidbody } from '../physics/components';
import { isTerrainDynamicsBlocking } from '../terrain/utils';
import { findNearestTerrainEntity } from '../terrain/index';

const placeQuery = defineQuery([PlacePending]);

const MAX_PLACE_HEIGHTMAP_DEFER_FRAMES = 600;
// Per-state so a dev HMR reload (or a second world) gets a fresh wait window
// instead of inheriting a maxed-out counter and placing on the flat
// placeholder surface before the heightmap decodes.
const _placeDeferByState = new WeakMap<State, number>();

/** Terrain has a heightmap URL but its sampler hasn't decoded yet. */
function isTerrainHeightmapPending(state: State): boolean {
  const tctx = getTerrainContext(state);
  for (const [, data] of tctx) {
    if (data.heightmapUrl && data.sampler.data === null) return true;
  }
  return false;
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

/** Deterministic rand: no scale jitter, no random yaw. */
const deterministicRand = (): number => 0;

function applyRootPlacement(
  state: State,
  eid: number,
  spawn: GroupSpawnDefaults,
  wx: number,
  wy: number,
  wz: number,
  normal: THREE.Vector3
): void {
  const euler = composeSpawnRotation(
    normal,
    spawn.alignToTerrain,
    0,
    [0, 0, 0]
  );
  Transform.posX[eid] = wx;
  Transform.posY[eid] = wy + spawn.baseYOffset;
  Transform.posZ[eid] = wz;
  Transform.eulerX[eid] = euler.x;
  Transform.eulerY[eid] = euler.y;
  Transform.eulerZ[eid] = euler.z;
  const q = eulerToQuaternion(euler.x, euler.y, euler.z);
  Transform.rotX[eid] = q.x;
  Transform.rotY[eid] = q.y;
  Transform.rotZ[eid] = q.z;
  Transform.rotW[eid] = q.w;
  Transform.dirty[eid] = 1;

  // Physics bodies live in world space: mirror the placed pose so the body is
  // created (or teleported) exactly where the entity landed on the terrain.
  if (state.hasComponent(eid, Rigidbody)) {
    Rigidbody.posX[eid] = Transform.posX[eid];
    Rigidbody.posY[eid] = Transform.posY[eid];
    Rigidbody.posZ[eid] = Transform.posZ[eid];
    Rigidbody.eulerX[eid] = euler.x;
    Rigidbody.eulerY[eid] = euler.y;
    Rigidbody.eulerZ[eid] = euler.z;
    Rigidbody.rotX[eid] = q.x;
    Rigidbody.rotY[eid] = q.y;
    Rigidbody.rotZ[eid] = q.z;
    Rigidbody.rotW[eid] = q.w;
  }
}

/**
 * Runs in the **first** simulation bucket so root `Transform` is updated from terrain **before**
 * {@link TransformHierarchySystem} propagates to children. If this ran after hierarchy, child
 * `WorldTransform` (e.g. particle emitters under `<entity place="…">`) would stay wrong until the
 * next frame — emitters looked “stuck” near world origin / wrong height.
 */
export const TerrainPlaceSystem: System = {
  group: 'simulation',
  first: true,
  update(state) {
    if (state.headless) return;

    const specs = getPlacementSpecs(state);
    if (specs.size === 0) return;

    // Wait for the heightmap to decode before placing, else entities land on the
    // flat placeholder surface and get buried once the real terrain rises.
    if (isTerrainHeightmapPending(state)) {
      const deferred = _placeDeferByState.get(state) ?? 0;
      if (deferred < MAX_PLACE_HEIGHTMAP_DEFER_FRAMES) {
        _placeDeferByState.set(state, deferred + 1);
        return;
      }
    }

    for (const eid of placeQuery(state.world)) {
      if (PlacePending.spawned[eid]) continue;

      const spec = specs.get(eid);
      if (!spec) {
        PlacePending.spawned[eid] = 1;
        continue;
      }

      const [ax, , az] = anchorOffset(state, eid);
      const wx = spec.atX + ax;
      const wz = spec.atZ + az;

      const terrainEid = findNearestTerrainEntity(state, wx, wz);
      if (isTerrainDynamicsBlocking(state, terrainEid || undefined)) continue;

      const s = sampleTerrainSurface(
        state,
        wx,
        wz,
        spec.spawn.surfaceEpsilon,
        spec.spawn.surfaceEpsilonAuto
      );
      if (!s) {
        console.warn(
          '[spawner] Place skipped: no terrain surface at (%.0f, %.0f)',
          wx,
          wz
        );
        PlacePending.spawned[eid] = 1;
        continue;
      }

      const maxSlope = Number.isFinite(spec.spawn.maxSlopeDeg)
        ? spec.spawn.maxSlopeDeg
        : 90;
      const acceptAnySlope = maxSlope >= 90 - 1e-6;

      if (!acceptAnySlope && !isNormalWithinSlopeLimit(s.normal, maxSlope)) {
        PlacePending.spawned[eid] = 1;
        continue;
      }

      const wy = s.worldY;

      if (spec.templates.length === 0) {
        applyRootPlacement(state, eid, spec.spawn, wx, wy, wz, s.normal);
        state.addComponent(eid, TerrainSpawned);
        TerrainSpawned.yOffset[eid] = Transform.posY[eid] - wy;
        TerrainSpawned.surfaceEpsilon[eid] = spec.spawn.surfaceEpsilon;
      } else {
        for (const template of spec.templates) {
          spawnTemplateAtTerrain(
            state,
            spec.spawn,
            deterministicRand,
            wx,
            wy,
            wz,
            template
          );
        }
      }

      PlacePending.spawned[eid] = 1;
    }
  },
};
