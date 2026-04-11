import type { Recipe } from '../../core';
import { PLAYER_BODY_DEFAULTS, PLAYER_COLLIDER_DEFAULTS } from './constants';

export const playerRecipe: Recipe = {
  name: 'Player',
  components: [
    'playerController',
    'character-movement',
    'transform',
    'rigidbody',
    'collider',
    'character-controller',
    'input-state',
    'respawn',
  ],
  overrides: {
    'rigidbody.eulerY': PLAYER_BODY_DEFAULTS.eulerY,
    'rigidbody.type': PLAYER_BODY_DEFAULTS.type,
    'rigidbody.ccd': PLAYER_BODY_DEFAULTS.ccd,
    'rigidbody.lock-rot-x': PLAYER_BODY_DEFAULTS.lockRotX,
    'rigidbody.lock-rot-z': PLAYER_BODY_DEFAULTS.lockRotZ,
    'collider.shape': PLAYER_COLLIDER_DEFAULTS.shape,
    'collider.radius': PLAYER_COLLIDER_DEFAULTS.radius,
    'collider.height': PLAYER_COLLIDER_DEFAULTS.height,
    'collider.friction': PLAYER_COLLIDER_DEFAULTS.friction,
    'collider.pos-offset-y': PLAYER_COLLIDER_DEFAULTS.posOffsetY,
  },
};

/** Same gameplay stack as {@link playerRecipe} plus GLB-driven visuals (no procedural box character). */
export const playerGltfRecipe: Recipe = {
  name: 'PlayerGLTF',
  components: [...(playerRecipe.components ?? []), 'playerGltfConfig'],
  overrides: playerRecipe.overrides,
};
