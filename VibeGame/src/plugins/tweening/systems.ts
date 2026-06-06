import type { System } from '../../core';
import { defineQuery, TIME_CONSTANTS } from '../../core';
import { KinematicMove, Rigidbody } from '../physics/components';
import { KinematicMovementSystem } from '../physics/systems';
import { Transform } from '../transforms/components';
import { EasingType, TweenAxis, TweenData } from './components';

const tweenQuery = defineQuery([TweenData]);

function applyEasing(t: number, easing: number): number {
  switch (easing) {
    case EasingType.EaseInOut:
      return t < 0.5 ? 2 * t * t : 1 - 2 * (1 - t) * (1 - t);
    case EasingType.EaseOutQuad:
      return 1 - (1 - t) * (1 - t);
    default:
      return t;
  }
}

export const TweenProcessingSystem: System = {
  group: 'fixed',
  before: [KinematicMovementSystem],
  update: (state) => {
    const dt = TIME_CONSTANTS.FIXED_TIMESTEP;

    for (const entity of tweenQuery(state.world)) {
      if (!TweenData.active[entity]) continue;

      const targetEid = TweenData.targetEntity[entity];
      if (targetEid === 0) continue;

      const axis = TweenData.axis[entity];
      const isRotationAxis =
        axis === TweenAxis.RotX || axis === TweenAxis.RotY || axis === TweenAxis.RotZ;
      if (!isRotationAxis && !state.hasComponent(targetEid, Rigidbody)) continue;

      TweenData.elapsed[entity] += dt;

      const elapsed = TweenData.elapsed[entity];
      const delay = TweenData.delay[entity];
      const duration = TweenData.duration[entity];
      const isLoop = TweenData.loop[entity] === 1;
      const isPingPong = TweenData.pingPong[entity] === 1;

      let rawProgress = duration > 0 ? (elapsed - delay) / duration : 1;

      if (rawProgress < 0) continue;

      if (isLoop) {
        rawProgress = rawProgress % 1;
        if (isPingPong) {
          const cycle = Math.floor(rawProgress * 2);
          if (cycle % 2 === 1) {
            rawProgress = 1 - (rawProgress * 2 - cycle);
          } else {
            rawProgress = rawProgress * 2 - cycle;
          }
        }
      } else {
        if (rawProgress >= 1) {
          rawProgress = 1;
          TweenData.active[entity] = 0;
        }
      }

      const t = applyEasing(rawProgress, TweenData.easing[entity]);
      const value = TweenData.from[entity] + (TweenData.to[entity] - TweenData.from[entity]) * t;

      if (axis === TweenAxis.None) continue;

      if (isRotationAxis) {
        if (!state.hasComponent(targetEid, Transform)) continue;
        Transform.dirty[targetEid] = 1;
        switch (axis) {
          case TweenAxis.RotX:
            Transform.eulerX[targetEid] = value;
            break;
          case TweenAxis.RotY:
            Transform.eulerY[targetEid] = value;
            break;
          case TweenAxis.RotZ:
            Transform.eulerZ[targetEid] = value;
            break;
        }
        continue;
      }

      if (!state.hasComponent(targetEid, KinematicMove)) {
        state.addComponent(targetEid, KinematicMove, {
          x: Rigidbody.posX[targetEid],
          y: Rigidbody.posY[targetEid],
          z: Rigidbody.posZ[targetEid],
        });
      }

      switch (axis) {
        case TweenAxis.PosX:
          KinematicMove.x[targetEid] = value;
          break;
        case TweenAxis.PosY:
          KinematicMove.y[targetEid] = value;
          break;
        case TweenAxis.PosZ:
          KinematicMove.z[targetEid] = value;
          break;
      }
    }
  },
};
