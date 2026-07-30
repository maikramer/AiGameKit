import type { Adapter, Plugin, Recipe } from '../../core';
import { Vegetation } from './components';
import { vegetationParser } from './parser';
import { VegetationPlannerSystem } from './planner-system';
import { VegetationWindSystem } from './wind';

/**
 * `<Vegetation meshes="…" density-per-km2="…" region-min="…" region-max="…">`
 * — dense ground cover via the shared static spawner path (InstancedMesh2) +
 * optional wind sway. With `smart` (default on), meshes are split into
 * grass/plant/flower layers that share cluster hubs.
 */
export const vegetationRecipe: Recipe = {
  name: 'Vegetation',
  components: ['transform', 'spawnerPending', 'vegetation'],
  parserAttributes: [
    'meshes',
    'density-per-km2',
    'count',
    'seed',
    'region-min',
    'region-max',
    'scale-min',
    'scale-max',
    'scale-axis-min',
    'scale-axis-max',
    'variation',
    'hue-jitter-deg',
    'saturation-min',
    'saturation-max',
    'brightness-min',
    'brightness-max',
    'contrast-min',
    'contrast-max',
    'variation-spatial',
    'max-slope-deg',
    'avoid-water',
    'avoid-road',
    'avoid-overlaps',
    'max-distance',
    'footprint-radius',
    'wind',
    'align-to-terrain',
    'ground-align',
    'random-yaw',
    'cluster-count',
    'cluster-radius',
    'smart',
    'flower-near-radius',
    'flower-density-ratio',
    'plant-density-ratio',
    'mesh-roles',
  ],
};

const windAdapter: Adapter = (entity, value) => {
  const s = String(value).trim().toLowerCase();
  Vegetation.wind[entity] = s === '0' || s === 'false' || s === 'no' ? 0 : 1;
};

export const VegetationPlugin: Plugin = {
  recipes: [vegetationRecipe],
  systems: [VegetationPlannerSystem, VegetationWindSystem],
  components: {
    vegetation: Vegetation,
  },
  config: {
    parsers: {
      Vegetation: vegetationParser,
    },
    defaults: {
      vegetation: {
        wind: 1,
        windRegistered: 0,
      },
      spawnerPending: {
        spawned: 0,
      },
    },
    adapters: {
      vegetation: {
        wind: windAdapter,
      },
    },
  },
};
