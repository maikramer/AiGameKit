import type { Plugin } from '../../core';
import {
  ColorOverLife,
  ParticleTexture,
  ParticleBurst,
  ParticleSystem,
  SizeOverLife,
} from './components';
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
    particleSystem: ParticleSystem,
    particleBurst: ParticleBurst,
    colorOverLife: ColorOverLife,
    sizeOverLife: SizeOverLife,
    particleTexture: ParticleTexture,
  },
  config: {
    defaults: {
      particleSystem: {
        preset: 0,
        rate: 20,
        lifetime: 2,
        size: 0.2,
        looping: 1,
        playing: 1,
        spawned: 0,
      },
      particleBurst: {
        preset: 2,
        count: 100,
        triggered: 0,
      },
      colorOverLife: {
        startR: 1,
        startG: 1,
        startB: 1,
        startA: 1,
        endR: 0,
        endG: 0,
        endB: 0,
        endA: 0,
      },
      sizeOverLife: {
        startSize: 1,
        endSize: 0,
      },
      particleTexture: {
        frameWidth: 0,
        frameHeight: 0,
        frames: 1,
        animationSpeed: 1,
      },
    },
    enums: {
      particleSystem: {
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
