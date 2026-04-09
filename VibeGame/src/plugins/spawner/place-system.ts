import { defineQuery, type State, type System } from '../../core';
import { PlacePending } from './components';
import { getPlacementSpecs } from './place-context';
import { spawnTemplateAtTerrain } from './spawn-template';
import { isNormalWithinSlopeLimit, sampleTerrainSurface } from './surface';
import { TransformHierarchySystem } from '../transforms';
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

export const TerrainPlaceSystem: System = {
  group: 'simulation',
  after: [TransformHierarchySystem],
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

      PlacePending.spawned[eid] = 1;
    }
  },
};
