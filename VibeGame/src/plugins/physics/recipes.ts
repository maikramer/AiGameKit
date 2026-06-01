import type { Recipe } from '../../core';
import { BodyType } from './components';

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
