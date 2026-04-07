import type { Plugin } from '../../core';
import { PlayerWaterState, SwimTriggerZone, Water } from './components';
import {
  SwimTriggerSystem,
  WaterBootstrapSystem,
  WaterInteractionSystem,
  WaterPhysicsSystem,
  WaterRenderSystem,
} from './systems';

export const WaterPlugin: Plugin = {
  recipes: [{ name: 'water', components: ['water', 'transform'] }],
  systems: [
    WaterBootstrapSystem,
    WaterRenderSystem,
    WaterPhysicsSystem,
    WaterInteractionSystem,
    SwimTriggerSystem,
  ],
  components: {
    water: Water,
    playerWaterState: PlayerWaterState,
    swimTriggerZone: SwimTriggerZone,
  },
  config: {
    defaults: {
      water: {
        size: 256,
        waterLevel: 5,
        opacity: 0.8,
        tintR: 0.1,
        tintG: 0.35,
        tintB: 0.5,
        waveSpeed: 1.0,
        waveScale: 0.3,
        wireframe: 0,
        underwaterFogColorR: 0.0,
        underwaterFogColorG: 0.05,
        underwaterFogColorB: 0.15,
        underwaterFogDensity: 0.15,
      },
      swimTriggerZone: {
        waterEntity: 0,
        enabled: 1,
        swimSpeed: 4,
        buoyancyForce: 8,
        maxSwimDepth: 6,
      },
    },
  },
};
