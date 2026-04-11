import type { Plugin } from '../../core';
import { PlayerController, PlayerGltfConfig } from './components';
import {
  playerGltfModelUrlAdapter,
  PlayerGltfAnimStateSystem,
  PlayerGltfEnsureHasAnimatorSystem,
  PlayerGltfSetupSystem,
} from './gltf-systems';
import { playerGltfRecipe, playerRecipe } from './recipes';
import {
  PlayerCameraLinkingSystem,
  PlayerGroundedSystem,
  PlayerMovementSystem,
} from './systems';

export const PlayerPlugin: Plugin = {
  systems: [
    PlayerGltfEnsureHasAnimatorSystem,
    PlayerGltfSetupSystem,
    PlayerCameraLinkingSystem,
    PlayerMovementSystem,
    PlayerGroundedSystem,
    PlayerGltfAnimStateSystem,
  ],
  recipes: [playerRecipe, playerGltfRecipe],
  components: {
    player: PlayerController,
    playerGltfConfig: PlayerGltfConfig,
  },
  config: {
    adapters: {
      'player-gltf-config': {
        'model-url': playerGltfModelUrlAdapter,
      },
    },
    defaults: {
      player: {
        speed: 5.3,
        jumpHeight: 2.3,
        rotationSpeed: 10,
        canJump: 1,
        isJumping: 0,
        jumpCooldown: 0,
        lastGroundedTime: 0,
        jumpBufferTime: -10000,
        cameraEntity: 0,
        inheritedVelX: 0,
        inheritedVelZ: 0,
      },
      'player-gltf-config': {
        modelUrlIndex: 0,
        loaded: 0,
        animatorRegistryIndex: 0,
      },
    },
  },
};
