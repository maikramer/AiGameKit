import type { Plugin } from '../../core';
import { ThirdPersonCamera } from './components';
import { thirdPersonCameraRecipe } from './recipes';
import { ThirdPersonCameraSystem } from './systems';

// Third-person *camera* rig (not the character movement controller — see ./context.md).
// Camera↔player linking lives in PlayerPlugin.PlayerCameraLinkingSystem.
export const ThirdPersonCameraPlugin: Plugin = {
  systems: [ThirdPersonCameraSystem],
  recipes: [thirdPersonCameraRecipe],
  components: {
    ThirdPersonCamera,
  },
  config: {
    defaults: {
      'third-person-camera': {
        distance: 12,
        height: 4,
        pitch: 0.3,
        positionSmooth: 0.08,
        mouseSensitivity: 0.003,
        minTerrainDistance: 1.0,
        followLag: 0.18,
        turnLag: 0.35,
      },
    },
  },
};

/** @deprecated Use {@link ThirdPersonCameraPlugin} instead. */
export const PlayerControllerPlugin = ThirdPersonCameraPlugin;
