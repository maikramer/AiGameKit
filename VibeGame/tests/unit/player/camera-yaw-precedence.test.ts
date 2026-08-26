import { beforeEach, describe, expect, it } from 'bun:test';
import {
  IsometricCamera,
  IsometricCameraPlugin,
  State,
  ThirdPersonCamera,
  ThirdPersonCameraPlugin,
} from 'vibegame';
import { InputState } from 'vibegame/input';
import { OrbitCamera, OrbitCameraPlugin } from 'vibegame/orbit-camera';
import { PlayerController, PlayerPlugin } from 'vibegame/player';
import { MainCamera } from 'vibegame/rendering';
import { Transform, TransformsPlugin } from 'vibegame/transforms';
import { PlayerCameraLinkingSystem } from '../../../src/plugins/player/systems';

/**
 * Anti-regression for the two hooks the isometric camera adds to the player
 * plugin. `player/systems.ts` is on the hot path of every shipped example, so
 * the contract under test is deliberately narrow: adding an isometric camera
 * must change nothing for a world that has none.
 */
describe('player camera precedence', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.registerPlugin(TransformsPlugin);
    state.registerPlugin(ThirdPersonCameraPlugin);
    state.registerPlugin(OrbitCameraPlugin);
    state.registerPlugin(IsometricCameraPlugin);
    state.registerPlugin(PlayerPlugin);
  });

  function makePlayer(): number {
    const player = state.createEntity();
    state.addComponent(player, PlayerController);
    return player;
  }

  /**
   * The three camera components have disjoint field sets, so they share no
   * usable static type — hence the `addRig` callback instead of passing the
   * component itself (which would need an unsound cast).
   */
  function makeCamera(addRig: (cam: number) => void): number {
    const cam = state.createEntity();
    addRig(cam);
    state.addComponent(cam, Transform);
    state.addComponent(cam, MainCamera);
    return cam;
  }

  const asThirdPerson = (cam: number) =>
    state.addComponent(cam, ThirdPersonCamera);
  const asOrbit = (cam: number) => state.addComponent(cam, OrbitCamera);
  const asIsometric = (cam: number) => state.addComponent(cam, IsometricCamera);

  describe('linking with no isometric camera present (unchanged behaviour)', () => {
    it('still links a third-person camera', () => {
      const player = makePlayer();
      const cam = makeCamera(asThirdPerson);

      PlayerCameraLinkingSystem.update!(state);

      expect(ThirdPersonCamera.target[cam]).toBe(player);
      expect(PlayerController.cameraEntity[player]).toBe(cam);
    });

    it('still links an orbit camera', () => {
      const player = makePlayer();
      const cam = makeCamera(asOrbit);

      PlayerCameraLinkingSystem.update!(state);

      expect(OrbitCamera.target[cam]).toBe(player);
      expect(OrbitCamera.inputSource[cam]).toBe(player);
      expect(PlayerController.cameraEntity[player]).toBe(cam);
    });

    it('still prefers third-person over orbit', () => {
      const player = makePlayer();
      const tp = makeCamera(asThirdPerson);
      const orbit = makeCamera(asOrbit);

      PlayerCameraLinkingSystem.update!(state);

      expect(PlayerController.cameraEntity[player]).toBe(tp);
      expect(OrbitCamera.target[orbit]).toBe(0);
    });

    it('adds InputState to the linked camera', () => {
      const player = makePlayer();
      const cam = makeCamera(asThirdPerson);

      PlayerCameraLinkingSystem.update!(state);

      expect(state.hasComponent(cam, InputState)).toBe(true);
      expect(player).toBeGreaterThan(0);
    });

    it('leaves an already-linked player alone', () => {
      const player = makePlayer();
      const cam = makeCamera(asThirdPerson);
      PlayerController.cameraEntity[player] = 999;

      PlayerCameraLinkingSystem.update!(state);

      expect(PlayerController.cameraEntity[player]).toBe(999);
      expect(ThirdPersonCamera.target[cam]).toBe(0);
    });

    it('does nothing when there is no camera at all', () => {
      const player = makePlayer();

      PlayerCameraLinkingSystem.update!(state);

      expect(PlayerController.cameraEntity[player]).toBe(0);
    });
  });

  describe('linking with an isometric camera', () => {
    it('links target and input source', () => {
      const player = makePlayer();
      const cam = makeCamera(asIsometric);

      PlayerCameraLinkingSystem.update!(state);

      expect(IsometricCamera.target[cam]).toBe(player);
      expect(IsometricCamera.inputSource[cam]).toBe(player);
      expect(PlayerController.cameraEntity[player]).toBe(cam);
      expect(state.hasComponent(cam, InputState)).toBe(true);
    });

    it('yields to a third-person camera when both are unlinked', () => {
      const player = makePlayer();
      const tp = makeCamera(asThirdPerson);
      const iso = makeCamera(asIsometric);

      PlayerCameraLinkingSystem.update!(state);

      expect(PlayerController.cameraEntity[player]).toBe(tp);
      expect(IsometricCamera.target[iso]).toBe(0);
    });

    it('wins over an orbit camera when both are unlinked', () => {
      const player = makePlayer();
      const orbit = makeCamera(asOrbit);
      const iso = makeCamera(asIsometric);

      PlayerCameraLinkingSystem.update!(state);

      expect(PlayerController.cameraEntity[player]).toBe(iso);
      expect(OrbitCamera.target[orbit]).toBe(0);
    });

    it('skips an isometric camera that already has a target', () => {
      const player = makePlayer();
      const iso = makeCamera(asIsometric);
      IsometricCamera.target[iso] = 4242;

      PlayerCameraLinkingSystem.update!(state);

      expect(IsometricCamera.target[iso]).toBe(4242);
      expect(PlayerController.cameraEntity[player]).toBe(0);
    });

    it('links only the first of several isometric cameras', () => {
      const player = makePlayer();
      const a = makeCamera(asIsometric);
      const b = makeCamera(asIsometric);

      PlayerCameraLinkingSystem.update!(state);

      expect(IsometricCamera.target[a]).toBe(player);
      expect(IsometricCamera.target[b]).toBe(0);
    });
  });
});
