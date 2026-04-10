import * as THREE from 'three';
import { eulerToQuaternion } from '../../core/math';
import { defineQuery, type State, type System } from '../../core';
import { PlacePending } from './components';
import type { GroupSpawnDefaults } from './profiles';
import { getPlacementSpecs } from './place-context';
import { spawnTemplateAtTerrain } from './spawn-template';
import { isNormalWithinSlopeLimit, sampleTerrainSurface } from './surface';
import { composeSpawnRotation } from './transform-merge';
import { Transform, WorldTransform } from '../transforms/components';

const placeQuery = defineQuery([PlacePending]);

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
  _state: State,
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

    for (const eid of placeQuery(state.world)) {
      if (PlacePending.spawned[eid]) continue;

      const spec = specs.get(eid);
      if (!spec) {
        PlacePending.spawned[eid] = 1;
        continue;
      }

      const surfaceProbe = sampleTerrainSurface(
        state,
        0,
        0,
        spec.spawn.surfaceEpsilon
      );
      if (!surfaceProbe) continue;

      const [ax, , az] = anchorOffset(state, eid);
      const wx = spec.atX + ax;
      const wz = spec.atZ + az;

      const s = sampleTerrainSurface(state, wx, wz, spec.spawn.surfaceEpsilon);
      if (!s) continue;

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
