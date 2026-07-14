import type { Adapter, Plugin, Recipe } from '../../core';
import { Vegetation } from './components';
import { vegetationParser } from './parser';
import { VegetationWindSystem } from './wind';

/**
 * `<Vegetation meshes="…" density-per-km2="…" region-min="…" region-max="…">`
 * — dense ground cover via the shared static spawner path (InstancedMesh2) +
 * optional wind sway.
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
    'max-slope-deg',
    'avoid-water',
    'avoid-overlaps',
    'max-distance',
    'footprint-radius',
    'wind',
    'align-to-terrain',
    'ground-align',
    'random-yaw',
  ],
};

const windAdapter: Adapter = (entity, value) => {
  const s = String(value).trim().toLowerCase();
  Vegetation.wind[entity] = s === '0' || s === 'false' || s === 'no' ? 0 : 1;
};

export const VegetationPlugin: Plugin = {
  recipes: [vegetationRecipe],
  systems: [VegetationWindSystem],
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
      // SpawnerPending defaults live in SpawnerPlugin; Vegetation also needs
      // the component present — recipe lists it, defaults come from spawner
      // if already registered, else provide a safe zero here.
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
