import type { Plugin } from '../../core';
import { ThirdPersonCamera } from './components';
import { thirdPersonCameraRecipe } from './recipes';
import { PlayerCameraLinkingSystem, ThirdPersonCameraSystem } from './systems';

// NOTE: despite the name, this is the third-person *camera* rig, not the player
// movement controller. The character controller is the sibling `player` plugin
// (PlayerPlugin / PlayerController). See ./context.md for the full distinction
// and the PlayerCameraLinkingSystem overlap between the two plugins.
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
        minTerrainDistance: 1.0,
        followLag: 0.18,
        turnLag: 0.35,
      },
    },
  },
};
