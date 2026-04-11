import type { Recipe } from '../../core';

export const particleEmitterRecipe: Recipe = {
  name: 'ParticleSystem',
  merge: true,
  components: ['transform', 'particleSystem'],
};

export const particleBurstRecipe: Recipe = {
  name: 'ParticleBurst',
  merge: true,
  components: ['transform', 'particleBurst'],
};
