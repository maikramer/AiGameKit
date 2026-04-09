import type { Plugin } from '../../core';
import { ParticlesBurst, ParticlesEmitter } from './components';
import { particleBurstRecipe, particleEmitterRecipe } from './recipes';
import {
  ParticleBootstrapSystem,
  ParticleBurstSystem,
  ParticleCleanupSystem,
  ParticleEmitSystem,
  ParticleRenderSystem,
} from './systems';

export const ParticlesPlugin: Plugin = {
  systems: [
    ParticleBootstrapSystem,
    ParticleEmitSystem,
    ParticleBurstSystem,
    ParticleRenderSystem,
    ParticleCleanupSystem,
  ],
  recipes: [particleEmitterRecipe, particleBurstRecipe],
  components: {
    particlesEmitter: ParticlesEmitter,
    particlesBurst: ParticlesBurst,
  },
  config: {
    defaults: {
      particlesEmitter: {
        preset: 0,
        rate: 20,
        lifetime: 2,
        size: 0.2,
        looping: 1,
        playing: 1,
        spawned: 0,
      },
      particlesBurst: {
        preset: 2,
        count: 100,
        triggered: 0,
      },
    },
    enums: {
      particlesEmitter: {
        preset: {
          fire: 0,
          smoke: 1,
          explosion: 2,
          sparks: 3,
          rain: 4,
          snow: 5,
          custom: 99,
        },
      },
    },
  },
};
