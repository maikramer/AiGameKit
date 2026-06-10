import type { Adapter, Plugin, State } from '../../core';
import { ParticleEmitter } from './components';
import { ParticleUpdateSystem } from './systems';
import { particleSystemRecipe, particleBurstRecipe } from './recipes';

function colorAdapter(
  rField: keyof typeof ParticleEmitter,
  gField: keyof typeof ParticleEmitter,
  bField: keyof typeof ParticleEmitter
): Adapter {
  return ((entity: number, value: string, _state: State) => {
    const num = parseInt(value.replace('#', ''), 16);
    ParticleEmitter[rField][entity] = ((num >> 16) & 0xff) / 255;
    ParticleEmitter[gField][entity] = ((num >> 8) & 0xff) / 255;
    ParticleEmitter[bField][entity] = (num & 0xff) / 255;
  }) as Adapter;
}

export const ParticlesPlugin: Plugin = {
  systems: [ParticleUpdateSystem],
  recipes: [particleSystemRecipe, particleBurstRecipe],
  components: { 'particle-emitter': ParticleEmitter },
  config: {
    defaults: {
      'particle-emitter': {
        active: 1,
        preset: 0,
        emissionRate: 50,
        duration: 5,
        startLifeMin: 1,
        startLifeMax: 3,
        startSpeedMin: 1,
        startSpeedMax: 5,
        startSizeMin: 0.1,
        startSizeMax: 0.5,
        startColorR: 1,
        startColorG: 0.5,
        startColorB: 0.1,
        startColorA: 1,
        worldSpace: 0,
        renderMode: 0,
        looping: 1,
        burst: 0,
        burstCount: 20,
        shapeRadius: 0.5,
        shapeAngle: 0.5,
      },
    },
    enums: {
      'particle-emitter': {
        preset: {
          fire: 0,
          rain: 1,
          snow: 2,
          smoke: 3,
          dust: 4,
          explosion: 5,
          sparks: 6,
          magic: 7,
          fireflies: 8,
        },
        'render-mode': {
          billboard: 0,
          stretched: 1,
          mesh: 2,
          trail: 3,
        },
      },
    },
    adapters: {
      'particle-emitter': {
        'start-color': colorAdapter(
          'startColorR',
          'startColorG',
          'startColorB'
        ) as Adapter,
      },
    },
  },
};
