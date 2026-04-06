import { defineQuery, type System } from '../../core';
import { GltfAnimator } from '../../extras/gltf-animator';
import { WorldTransform } from '../transforms';
import { syncEulerFromQuaternion } from '../transforms/utils';
import { GltfAnimationState } from './components';

export const animatorRegistry = new Map<number, GltfAnimator>();

let nextRegistryIndex = 1;

export function registerAnimator(animator: GltfAnimator): number {
  const idx = nextRegistryIndex++;
  animatorRegistry.set(idx, animator);
  return idx;
}

const gltfAnimQuery = defineQuery([GltfAnimationState]);

export const GltfAnimationUpdateSystem: System = {
  group: 'draw',
  update: (state) => {
    const dt = state.time.deltaTime;

    for (const eid of gltfAnimQuery(state.world)) {
      const idx = GltfAnimationState.registryIndex[eid];
      if (idx === 0) {
        continue;
      }

      const animator = animatorRegistry.get(idx);
      if (!animator) {
        continue;
      }

      animator.update(dt);

      if (!state.hasComponent(eid, WorldTransform)) {
        continue;
      }

      const root = animator.root;
      WorldTransform.posX[eid] = root.position.x;
      WorldTransform.posY[eid] = root.position.y;
      WorldTransform.posZ[eid] = root.position.z;
      WorldTransform.rotX[eid] = root.quaternion.x;
      WorldTransform.rotY[eid] = root.quaternion.y;
      WorldTransform.rotZ[eid] = root.quaternion.z;
      WorldTransform.rotW[eid] = root.quaternion.w;
      syncEulerFromQuaternion(WorldTransform, eid);
    }
  },
};
