import {
  defineSystem,
  defineQueryLive,
  type State,
  type System,
} from '../../core';
import { GltfAnimator } from '../../extras/gltf-animator';
import { MainCamera, threeCameras } from '../rendering';
import { Transform, WorldTransform } from '../transforms';
import { syncEulerFromQuaternion } from '../transforms/utils';
import { GltfAnimationState } from './components';

/**
 * Per-State animator registries: two States on one page must not share
 * indices, and disposing one State must not dispose the other's animators.
 */
const registryByState = new WeakMap<State, Map<number, GltfAnimator>>();
const nextIndexByState = new WeakMap<State, number>();

function getRegistry(state: State): Map<number, GltfAnimator> {
  let r = registryByState.get(state);
  if (!r) {
    r = new Map();
    registryByState.set(state, r);
  }
  return r;
}

export function registerAnimator(state: State, animator: GltfAnimator): number {
  const next = nextIndexByState.get(state) ?? 1;
  nextIndexByState.set(state, next + 1);
  getRegistry(state).set(next, animator);
  return next;
}

/** Drop and dispose an animator (call from the owner entity's onDestroy). */
export function unregisterAnimator(state: State, idx: number): void {
  const animator = getRegistry(state).get(idx);
  if (!animator) return;
  animator.dispose();
  getRegistry(state).delete(idx);
}

/** Animator for a registered entity, or undefined (used by update systems). */
export function getAnimator(
  state: State,
  idx: number
): GltfAnimator | undefined {
  return getRegistry(state).get(idx);
}

const gltfAnimQuery = defineQueryLive([GltfAnimationState]);
const mainCameraQuery = defineQueryLive([MainCamera]);

/** Beyond this: skip mixer entirely (pose frozen until closer). */
const ANIM_SKIP_DIST_SQ = 220 * 220;
/** Beyond this: update mixer every other frame. */
const ANIM_HALF_DIST_SQ = 140 * 140;

export const GltfAnimationUpdateSystem: System = defineSystem({
  name: 'GltfAnimationUpdateSystem',
  group: 'draw',
  update: (state) => {
    const dt = state.time.deltaTime;
    const frame = state.time.frameCount;

    let camX = 0;
    let camZ = 0;
    let hasCam = false;
    const cams = mainCameraQuery(state.world);
    if (cams.length > 0) {
      const cam = threeCameras.get(cams[0]!);
      if (cam) {
        camX = cam.position.x;
        camZ = cam.position.z;
        hasCam = true;
      }
    }

    for (const eid of gltfAnimQuery(state.world)) {
      const idx = GltfAnimationState.registryIndex[eid];
      if (idx === 0) {
        continue;
      }

      const animator = getRegistry(state).get(idx);
      if (!animator) {
        continue;
      }

      // Far skinned meshes: skip or half-rate mixer (CPU skinning dominates).
      if (hasCam && state.hasComponent(eid, WorldTransform)) {
        const dx = WorldTransform.posX[eid] - camX;
        const dz = WorldTransform.posZ[eid] - camZ;
        const distSq = dx * dx + dz * dz;
        if (distSq > ANIM_SKIP_DIST_SQ) {
          continue;
        }
        if (distSq > ANIM_HALF_DIST_SQ && (frame & 1) === 1) {
          continue;
        }
      }

      animator.update(dt);

      if (!state.hasComponent(eid, WorldTransform)) {
        continue;
      }
      // Only a root-motion animator owns the entity's world pose. For every
      // other rig the root is a child of the group the loader already placed,
      // so its (identity) local transform is not where the entity is — writing
      // it back parked the whole NPC cast on the world origin.
      if (GltfAnimationState.rootMotion[eid] !== 1) {
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
  dispose(state: State) {
    for (const animator of getRegistry(state).values()) {
      animator.dispose();
    }
    registryByState.delete(state);
    nextIndexByState.delete(state);
  },
});
