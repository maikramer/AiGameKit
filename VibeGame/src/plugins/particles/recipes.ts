import type { Recipe } from '../../core';

export const particleEmitterRecipe: Recipe = {
  name: 'particle-emitter',
  components: ['transform', 'particlesEmitter'],
};

export const particleBurstRecipe: Recipe = {
  name: 'particle-burst',
  components: ['transform', 'particlesBurst'],
};
