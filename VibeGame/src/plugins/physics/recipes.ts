import type { Recipe } from '../../core';
import { BodyType, ColliderShape } from './components';

/** Capsule sized to NavMeshAgent defaults (radius 0.4, height 1.0). */
const CREATURE_COLLIDER_DEFAULTS = {
  shape: ColliderShape.Capsule,
  radius: 0.4,
  /** Cylinder segment so total capsule height = height + 2·radius = 1.0. */
  height: 0.2,
  friction: 0,
  posOffsetY: 0.5,
} as const;

/**
 * Kinematic CCT stack for AI creatures (enemies). Same ground authority as
 * Player, without input / PlayerController / respawn.
 */
export const creatureRecipe: Recipe = {
  name: 'Creature',
  components: [
    'transform',
    'rigidbody',
    'collider',
    'character-controller',
    'character-movement',
  ],
  merge: true,
  /** Same as GameObject: `place` + analyze-only `overlap-max`. */
  parserAttributes: ['place', 'overlap-max'],
  overrides: {
    'rigidbody.type': BodyType.KinematicPositionBased,
    'rigidbody.mass': 1,
    'rigidbody.gravity-scale': 1,
    'rigidbody.ccd': 1,
    'rigidbody.lock-rot-x': 1,
    'rigidbody.lock-rot-z': 1,
    // Same snap as player defaults — 0 let CCT fall through HF gaps (roads/pads)
    // after collider refit and stay buried under the sampler surface.
    'character-controller.snap-dist': 0.5,
    'collider.shape': CREATURE_COLLIDER_DEFAULTS.shape,
    'collider.radius': CREATURE_COLLIDER_DEFAULTS.radius,
    'collider.height': CREATURE_COLLIDER_DEFAULTS.height,
    'collider.friction': CREATURE_COLLIDER_DEFAULTS.friction,
    'collider.pos-offset-y': CREATURE_COLLIDER_DEFAULTS.posOffsetY,
  },
};

const physicsPartRecipe: Recipe = {
  name: 'physics-part',
  components: ['rigidbody', 'collider', 'transform', 'renderer'],
};

export const staticPartRecipe: Recipe = {
  ...physicsPartRecipe,
  name: 'static-part',
  overrides: {
    'rigidbody.type': BodyType.Fixed,
    'rigidbody.mass': 0,
    'rigidbody.gravity-scale': 0,
  },
};

export const dynamicPartRecipe: Recipe = {
  ...physicsPartRecipe,
  name: 'dynamic-part',
  overrides: {
    'rigidbody.type': BodyType.Dynamic,
  },
};

export const kinematicPartRecipe: Recipe = {
  ...physicsPartRecipe,
  name: 'kinematic-part',
  overrides: {
    'rigidbody.type': BodyType.KinematicVelocityBased,
    'rigidbody.gravity-scale': 0,
  },
};
