import { describe, expect, it } from 'bun:test';
import { State } from '../../../src/core';
import { spawnInstancedGltf } from '../../../src/plugins/gltf-xml/spawn';
import { isGltfInstanced } from '../../../src/plugins/gltf-xml/auto-instance';
import { GltfPending } from '../../../src/plugins/gltf-xml/components';
import { getGltfUrl } from '../../../src/plugins/gltf-xml/context';
import { Transform } from '../../../src/plugins/transforms/components';
import { DistanceCull } from '../../../src/plugins/rendering/components';

describe('spawnInstancedGltf', () => {
  it('creates an entity routed to the instancing pool', () => {
    const state = new State();
    const eid = spawnInstancedGltf(state, {
      url: '/props/barrier.glb',
      x: 10,
      y: 2,
      z: -4,
    });
    expect(getGltfUrl(state, eid)).toBe('/props/barrier.glb');
    expect(isGltfInstanced(state, eid)).toBe(true);
    expect(state.hasComponent(eid, GltfPending)).toBe(true);
    expect(GltfPending.loaded[eid]).toBe(0);
    expect(Transform.posX[eid]).toBe(10);
    expect(Transform.posY[eid]).toBe(2);
    expect(Transform.posZ[eid]).toBe(-4);
  });

  it('applies a uniform scale', () => {
    const state = new State();
    const eid = spawnInstancedGltf(state, {
      url: '/props/tree.glb',
      x: 0,
      y: 0,
      z: 0,
      scale: 2.5,
    });
    expect(Transform.scaleX[eid]).toBe(2.5);
    expect(Transform.scaleY[eid]).toBe(2.5);
    expect(Transform.scaleZ[eid]).toBe(2.5);
  });

  it('turns a yaw into the transform quaternion', () => {
    const state = new State();
    const eid = spawnInstancedGltf(state, {
      url: '/props/sign.glb',
      x: 0,
      y: 0,
      z: 0,
      yaw: Math.PI / 2,
    });
    expect(Transform.rotY[eid]).toBeCloseTo(Math.SQRT1_2, 6);
    expect(Transform.rotW[eid]).toBeCloseTo(Math.SQRT1_2, 6);
  });

  it('takes a full quaternion over a yaw', () => {
    const state = new State();
    const eid = spawnInstancedGltf(state, {
      url: '/props/debris.glb',
      x: 0,
      y: 0,
      z: 0,
      yaw: 1,
      quaternion: [0.5, 0.5, 0.5, 0.5],
    });
    expect(Transform.rotX[eid]).toBe(0.5);
    expect(Transform.rotY[eid]).toBe(0.5);
    expect(Transform.rotZ[eid]).toBe(0.5);
    expect(Transform.rotW[eid]).toBe(0.5);
  });

  it('adds DistanceCull only when a cull distance is given', () => {
    const state = new State();
    const near = spawnInstancedGltf(state, {
      url: '/props/a.glb',
      x: 0,
      y: 0,
      z: 0,
      cullDistance: 120,
    });
    const always = spawnInstancedGltf(state, {
      url: '/props/a.glb',
      x: 0,
      y: 0,
      z: 0,
    });
    expect(state.hasComponent(near, DistanceCull)).toBe(true);
    expect(DistanceCull.maxDistance[near]).toBe(120);
    expect(state.hasComponent(always, DistanceCull)).toBe(false);
  });

  it('names the entity when asked', () => {
    const state = new State();
    const eid = spawnInstancedGltf(state, {
      url: '/props/gantry.glb',
      x: 0,
      y: 0,
      z: 0,
      name: 'start-gantry',
    });
    expect(state.getEntityByName('start-gantry')).toBe(eid);
  });
});
