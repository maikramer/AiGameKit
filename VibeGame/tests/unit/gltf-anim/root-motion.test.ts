import { afterEach, beforeEach, describe, expect, it } from 'bun:test';
import { Object3D } from 'three';

import { State } from '../../../src/core/ecs/state';
import type { GltfAnimator } from '../../../src/extras/gltf-animator';
import { GltfAnimationState } from '../../../src/plugins/gltf-anim/components';
import {
  GltfAnimationUpdateSystem,
  registerAnimator,
  unregisterAnimator,
} from '../../../src/plugins/gltf-anim/systems';
import { Transform, WorldTransform } from '../../../src/plugins/transforms';

/**
 * Regression guard for the "all NPCs stand on the world origin" bug: an
 * auto-idled `<GLTFLoader>` NPC gets an animator whose root is a *child* of
 * the group the loader placed, so the root's local transform is the identity.
 * Copying it into WorldTransform every frame teleported the whole cast to
 * (0,0,0) and re-dirtied Transform so the hierarchy could never win.
 */

function makeAnimator(root: Object3D): GltfAnimator {
  return {
    root,
    update: () => {},
    dispose: () => {},
  } as unknown as GltfAnimator;
}

describe('GltfAnimationUpdateSystem world-pose write-back', () => {
  let state: State;
  let eid: number;
  let idx: number;

  beforeEach(() => {
    state = new State();
    state.registerComponent('transform', Transform);
    state.registerComponent('world-transform', WorldTransform);
    state.registerComponent('gltf-animation-state', GltfAnimationState);

    eid = state.createEntity();
    state.addComponent(eid, Transform);
    state.addComponent(eid, WorldTransform);
    state.addComponent(eid, GltfAnimationState);

    WorldTransform.posX[eid] = -8;
    WorldTransform.posY[eid] = 35.8;
    WorldTransform.posZ[eid] = 30;
    WorldTransform.rotW[eid] = 1;
    Transform.dirty[eid] = 0;
  });

  afterEach(() => {
    if (idx) unregisterAnimator(state, idx);
    idx = 0;
  });

  it('leaves the placed world pose alone for a non-root-motion animator', () => {
    // Identity root: a lod0 group sitting inside an already-placed GLB group.
    idx = registerAnimator(state, makeAnimator(new Object3D()));
    GltfAnimationState.registryIndex[eid] = idx;
    GltfAnimationState.rootMotion[eid] = 0;

    GltfAnimationUpdateSystem.update!(state);

    expect(WorldTransform.posX[eid]).toBe(-8);
    expect(WorldTransform.posZ[eid]).toBe(30);
    expect(Transform.dirty[eid]).toBe(0);
  });

  it('copies the root pose back when root motion is opted in', () => {
    const root = new Object3D();
    root.position.set(4, 1, -2);
    idx = registerAnimator(state, makeAnimator(root));
    GltfAnimationState.registryIndex[eid] = idx;
    GltfAnimationState.rootMotion[eid] = 1;

    GltfAnimationUpdateSystem.update!(state);

    expect(WorldTransform.posX[eid]).toBeCloseTo(4, 5);
    expect(WorldTransform.posY[eid]).toBeCloseTo(1, 5);
    expect(WorldTransform.posZ[eid]).toBeCloseTo(-2, 5);
    expect(Transform.dirty[eid]).toBe(1);
  });

  it('does not re-dirty Transform when a root-motion root has not moved', () => {
    const root = new Object3D();
    root.position.set(-8, 35.8, 30);
    idx = registerAnimator(state, makeAnimator(root));
    GltfAnimationState.registryIndex[eid] = idx;
    GltfAnimationState.rootMotion[eid] = 1;

    GltfAnimationUpdateSystem.update!(state);

    expect(Transform.dirty[eid]).toBe(0);
  });

  it('ignores an entity with no registered animator', () => {
    GltfAnimationState.registryIndex[eid] = 0;
    expect(() => GltfAnimationUpdateSystem.update!(state)).not.toThrow();
    expect(WorldTransform.posX[eid]).toBe(-8);
  });
});
