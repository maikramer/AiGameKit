import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'aigamekit-vibegame';
import { InputState } from 'aigamekit-vibegame/input';
import {
  OrbitCamera,
  OrbitCameraPlugin,
} from 'aigamekit-vibegame/orbit-camera';
import { ThirdPersonCamera, ThirdPersonCameraPlugin } from 'aigamekit-vibegame';
import { PlayerController, PlayerPlugin } from 'aigamekit-vibegame/player';
import { PlayerCameraLinkingSystem } from '../../../src/plugins/player/systems';
import { MainCamera } from 'aigamekit-vibegame/rendering';
import { Transform, TransformsPlugin } from 'aigamekit-vibegame/transforms';

describe('PlayerCameraLinkingSystem', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.registerPlugin(TransformsPlugin);
    state.registerPlugin(ThirdPersonCameraPlugin);
    state.registerPlugin(OrbitCameraPlugin);
    state.registerPlugin(PlayerPlugin);
  });

  it('links unlinked ThirdPersonCamera to player and adds InputState on camera', () => {
    const player = state.createEntity();
    state.addComponent(player, PlayerController);

    const cam = state.createEntity();
    state.addComponent(cam, ThirdPersonCamera);
    state.addComponent(cam, Transform);
    state.addComponent(cam, MainCamera);

    PlayerCameraLinkingSystem.update!(state);

    expect(ThirdPersonCamera.target[cam]).toBe(player);
    expect(PlayerController.cameraEntity[player]).toBe(cam);
    expect(state.hasComponent(cam, InputState)).toBe(true);
  });

  it('links unlinked OrbitCamera when no third-person camera exists', () => {
    const player = state.createEntity();
    state.addComponent(player, PlayerController);

    const cam = state.createEntity();
    state.addComponent(cam, OrbitCamera);
    state.addComponent(cam, Transform);
    state.addComponent(cam, MainCamera);

    PlayerCameraLinkingSystem.update!(state);

    expect(OrbitCamera.target[cam]).toBe(player);
    expect(OrbitCamera.inputSource[cam]).toBe(player);
    expect(PlayerController.cameraEntity[player]).toBe(cam);
    expect(state.hasComponent(cam, InputState)).toBe(true);
  });

  it('prefers third-person over orbit when both are unlinked', () => {
    const player = state.createEntity();
    state.addComponent(player, PlayerController);

    const tpCam = state.createEntity();
    state.addComponent(tpCam, ThirdPersonCamera);
    state.addComponent(tpCam, Transform);
    state.addComponent(tpCam, MainCamera);

    const orbitCam = state.createEntity();
    state.addComponent(orbitCam, OrbitCamera);
    state.addComponent(orbitCam, Transform);
    state.addComponent(orbitCam, MainCamera);

    PlayerCameraLinkingSystem.update!(state);

    expect(PlayerController.cameraEntity[player]).toBe(tpCam);
    expect(ThirdPersonCamera.target[tpCam]).toBe(player);
    expect(OrbitCamera.target[orbitCam]).toBe(0);
  });

  it('does not relink a player that already has cameraEntity', () => {
    const player = state.createEntity();
    state.addComponent(player, PlayerController);

    const existingCam = state.createEntity();
    PlayerController.cameraEntity[player] = existingCam;

    const cam = state.createEntity();
    state.addComponent(cam, ThirdPersonCamera);

    PlayerCameraLinkingSystem.update!(state);

    expect(ThirdPersonCamera.target[cam]).toBe(0);
    expect(PlayerController.cameraEntity[player]).toBe(existingCam);
  });
});
