import { defineQuery, type State, type System } from '../../core';
import { GltfAnimator } from '../../extras/gltf-animator';
import { Transform, WorldTransform } from '../transforms';
import { syncEulerFromQuaternion } from '../transforms/utils';
import { GltfAnimationState } from './components';

export const animatorRegistry = new Map<number, GltfAnimator>();

let nextRegistryIndex = 1;

export function registerAnimator(animator: GltfAnimator): number {
  const idx = nextRegistryIndex++;
  animatorRegistry.set(idx, animator);
  return idx;
}

/** Drop and dispose an animator (call from the owner entity's onDestroy). */
export function unregisterAnimator(idx: number): void {
  const animator = animatorRegistry.get(idx);
  if (!animator) return;
  animator.dispose();
  animatorRegistry.delete(idx);
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
      const px = root.position.x;
      const py = root.position.y;
      const pz = root.position.z;
      const qx = root.quaternion.x;
      const qy = root.quaternion.y;
      const qz = root.quaternion.z;
      const qw = root.quaternion.w;
      const moved =
        Math.abs(WorldTransform.posX[eid] - px) > 1e-5 ||
        Math.abs(WorldTransform.posY[eid] - py) > 1e-5 ||
        Math.abs(WorldTransform.posZ[eid] - pz) > 1e-5 ||
        Math.abs(WorldTransform.rotX[eid] - qx) > 1e-5 ||
        Math.abs(WorldTransform.rotY[eid] - qy) > 1e-5 ||
        Math.abs(WorldTransform.rotZ[eid] - qz) > 1e-5 ||
        Math.abs(WorldTransform.rotW[eid] - qw) > 1e-5;
      WorldTransform.posX[eid] = px;
      WorldTransform.posY[eid] = py;
      WorldTransform.posZ[eid] = pz;
      WorldTransform.rotX[eid] = qx;
      WorldTransform.rotY[eid] = qy;
      WorldTransform.rotZ[eid] = qz;
      WorldTransform.rotW[eid] = qw;
      if (moved) {
        syncEulerFromQuaternion(WorldTransform, eid);
        if (state.hasComponent(eid, Transform)) {
          Transform.dirty[eid] = 1;
        }
      }
    }
  },
  dispose(_state: State) {
    for (const animator of animatorRegistry.values()) {
      if (typeof animator.dispose === 'function') {
        try {
          animator.dispose();
        } catch {
          // Animator may already be disposed.
        }
      }
    }
    animatorRegistry.clear();
    // 0 is the "no animator" sentinel in GltfAnimationState.registryIndex.
    nextRegistryIndex = 1;
  },
};
