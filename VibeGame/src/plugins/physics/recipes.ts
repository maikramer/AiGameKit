import type { Recipe } from '../../core';
import { BodyType } from './components';

export const rigidbodyRecipe: Recipe = {
  name: 'Rigidbody',
  merge: true,
  components: ['rigidbody', 'transform'],
};

export const colliderRecipe: Recipe = {
  name: 'Collider',
  merge: true,
  components: ['collider', 'transform'],
};

export const dynamicPartRecipe: Recipe = {
  name: 'dynamic-part',
  components: ['rigidbody', 'collider', 'transform', 'meshRenderer'],
  overrides: {
    'rigidbody.type': BodyType.Dynamic,
    'rigidbody.mass': 1,
    'rigidbody.gravity-scale': 1,
    'rigidbody.rot-w': 1,
  },
};
