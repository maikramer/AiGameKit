import { beforeEach, describe, expect, it } from 'bun:test';
import {
  State,
  ThirdPersonCamera,
  ThirdPersonCameraPlugin,
  clearShadowFocusEntity,
  getShadowFocusEntity,
  setShadowFocusEntity,
} from 'vibegame';
import { MainCamera, RenderingPlugin } from 'vibegame/rendering';
import {
  Transform,
  TransformsPlugin,
  WorldTransform,
} from 'vibegame/transforms';
import { resolveShadowCenter } from '../../../src/plugins/rendering/systems';

/**
 * The shadow frustum is only 32 m across at the default map size, so where it
 * is centred decides whether the player casts a shadow at all. These tests pin
 * the whole fallback chain, because the isometric camera adds a new first
 * branch to it and every existing game depends on the rest staying put.
 */
describe('shadow focus', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.registerPlugin(TransformsPlugin);
    state.registerPlugin(RenderingPlugin);
    state.registerPlugin(ThirdPersonCameraPlugin);
    clearShadowFocusEntity(state);
  });

  function makePositioned(x: number, y: number, z: number): number {
    const eid = state.createEntity();
    state.addComponent(eid, Transform);
    state.addComponent(eid, WorldTransform);
    WorldTransform.posX[eid] = x;
    WorldTransform.posY[eid] = y;
    WorldTransform.posZ[eid] = z;
    WorldTransform.rotW[eid] = 1;
    return eid;
  }

  describe('registry', () => {
    it('reports 0 when nothing is focused', () => {
      expect(getShadowFocusEntity(state)).toBe(0);
    });

    it('round-trips a focus entity', () => {
      setShadowFocusEntity(state, 42);
      expect(getShadowFocusEntity(state)).toBe(42);
    });

    it('overwrites a previous focus', () => {
      setShadowFocusEntity(state, 42);
      setShadowFocusEntity(state, 7);
      expect(getShadowFocusEntity(state)).toBe(7);
    });

    it('clears back to 0', () => {
      setShadowFocusEntity(state, 42);
      clearShadowFocusEntity(state);
      expect(getShadowFocusEntity(state)).toBe(0);
    });

    it('keeps focus per state', () => {
      const other = new State();
      setShadowFocusEntity(state, 42);
      expect(getShadowFocusEntity(other)).toBe(0);
    });
  });

  describe('resolveShadowCenter', () => {
    it('centres on the focus entity when one is set', () => {
      const hero = makePositioned(12, 3, -8);
      setShadowFocusEntity(state, hero);

      const c = resolveShadowCenter(state);

      expect(c.x).toBeCloseTo(12, 5);
      expect(c.y).toBeCloseTo(3, 5);
      expect(c.z).toBeCloseTo(-8, 5);
    });

    it('beats a third-person camera target', () => {
      const heroTp = makePositioned(0, 0, 0);
      const cam = state.createEntity();
      state.addComponent(cam, ThirdPersonCamera);
      ThirdPersonCamera.target[cam] = heroTp;

      const focus = makePositioned(99, 0, 99);
      setShadowFocusEntity(state, focus);

      const c = resolveShadowCenter(state);
      expect(c.x).toBeCloseTo(99, 5);
      expect(c.z).toBeCloseTo(99, 5);
    });

    it('ignores a focus entity without a WorldTransform (falls through)', () => {
      const bare = state.createEntity();
      setShadowFocusEntity(state, bare);

      const heroTp = makePositioned(5, 1, 5);
      const cam = state.createEntity();
      state.addComponent(cam, ThirdPersonCamera);
      ThirdPersonCamera.target[cam] = heroTp;

      const c = resolveShadowCenter(state);
      expect(c.x).toBeCloseTo(5, 5);
      expect(c.z).toBeCloseTo(5, 5);
    });

    it('ignores focus entity 0', () => {
      setShadowFocusEntity(state, 0);
      const heroTp = makePositioned(5, 1, 5);
      const cam = state.createEntity();
      state.addComponent(cam, ThirdPersonCamera);
      ThirdPersonCamera.target[cam] = heroTp;

      const c = resolveShadowCenter(state);
      expect(c.x).toBeCloseTo(5, 5);
    });

    it('still uses the third-person target when unfocused', () => {
      const hero = makePositioned(-4, 2, 6);
      const cam = state.createEntity();
      state.addComponent(cam, ThirdPersonCamera);
      ThirdPersonCamera.target[cam] = hero;

      const c = resolveShadowCenter(state);
      expect(c.x).toBeCloseTo(-4, 5);
      expect(c.y).toBeCloseTo(2, 5);
      expect(c.z).toBeCloseTo(6, 5);
    });

    it('still biases forward from a plain main camera when unfocused', () => {
      const cam = makePositioned(0, 10, 0);
      state.addComponent(cam, MainCamera);

      const c = resolveShadowCenter(state);
      // Camera looks down -Z by default → the box is pushed ahead of it.
      expect(c.z).toBeLessThan(0);
    });

    it('restores the previous result exactly after clearing', () => {
      const hero = makePositioned(-4, 2, 6);
      const cam = state.createEntity();
      state.addComponent(cam, ThirdPersonCamera);
      ThirdPersonCamera.target[cam] = hero;

      const before = resolveShadowCenter(state).clone();
      setShadowFocusEntity(state, makePositioned(80, 0, 80));
      resolveShadowCenter(state);
      clearShadowFocusEntity(state);
      const after = resolveShadowCenter(state);

      expect(after.x).toBe(before.x);
      expect(after.y).toBe(before.y);
      expect(after.z).toBe(before.z);
    });

    it('tracks the focus entity as it moves', () => {
      const hero = makePositioned(0, 0, 0);
      setShadowFocusEntity(state, hero);
      expect(resolveShadowCenter(state).x).toBeCloseTo(0, 5);

      WorldTransform.posX[hero] = 25;
      expect(resolveShadowCenter(state).x).toBeCloseTo(25, 5);
    });
  });
});
