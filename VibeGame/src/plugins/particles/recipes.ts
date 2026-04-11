import type { Recipe } from '../../core';

export const particleEmitterRecipe: Recipe = {
  name: 'ParticleSystem',
  components: ['transform', 'particleSystem'],
};

export const particleBurstRecipe: Recipe = {
  name: 'ParticleBurst',
  components: ['transform', 'particleBurst'],
};
