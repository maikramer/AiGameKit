import type { Recipe } from '../../core';

export const particleSystemRecipe: Recipe = {
  name: 'ParticleSystem',
  components: ['particle-emitter', 'transform'],
};

export const particleBurstRecipe: Recipe = {
  name: 'ParticleBurst',
  components: ['particle-emitter', 'transform'],
  overrides: {
    'particle-emitter.burst': 1,
    'particle-emitter.looping': 0,
  },
};
