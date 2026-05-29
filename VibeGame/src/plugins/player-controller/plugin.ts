import type { Plugin } from '../../core';
import { ThirdPersonCamera } from './components';
import { thirdPersonCameraRecipe } from './recipes';
import { PlayerCameraLinkingSystem, ThirdPersonCameraSystem } from './systems';

export const PlayerControllerPlugin: Plugin = {
  systems: [PlayerCameraLinkingSystem, ThirdPersonCameraSystem],
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
      },
    },
  },
};
