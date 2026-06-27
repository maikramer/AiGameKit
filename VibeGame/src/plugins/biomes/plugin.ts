import type { Plugin } from '../../core';
import { NULL_ENTITY } from '../../core/ecs/constants';
import { ActiveBiome, BiomeRegion } from './components';
import { biomeRegionRecipe } from './recipes';
import { biomeRegionParser } from './parser';
import {
  ambientAdapter,
  bgmLayerAdapter,
  fogColorAdapter,
  fogDensityAdapter,
  polygonAdapter,
  tintAdapter,
  typeAdapter,
} from './adapters';
import { BiomeDetectionSystem } from './systems';

export const BiomesPlugin: Plugin = {
  recipes: [biomeRegionRecipe],
  systems: [BiomeDetectionSystem],
  components: {
    'biome-region': BiomeRegion,
    'active-biome': ActiveBiome,
  },
  config: {
    parsers: {
      BiomeRegion: biomeRegionParser,
    },
    defaults: {
      'biome-region': {
        polyMinX: 0,
        polyMinZ: 0,
        polyMaxX: 0,
        polyMaxZ: 0,
        type: 0,
        tintR: 1,
        tintG: 1,
        tintB: 1,
        fogColor: 0,
        fogDensity: 0,
        ambientR: 1,
        ambientG: 1,
        ambientB: 1,
        bgmLayer: 0,
      },
      'active-biome': {
        current: NULL_ENTITY,
        target: NULL_ENTITY,
        blend: 1,
      },
    },
    adapters: {
      'biome-region': {
        polygon: polygonAdapter,
        tint: tintAdapter,
        ambient: ambientAdapter,
        'fog-color': fogColorAdapter,
        'fog-density': fogDensityAdapter,
        type: typeAdapter,
        'bgm-layer': bgmLayerAdapter,
      },
    },
  },
};
