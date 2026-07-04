import type { Adapter, Plugin, Recipe } from '../../core';
import { WeatherComponent } from './components';
import { WeatherSystem } from './systems';

/**
 * `<Weather wind="0.7 0.3" wind-strength="1.5" clouds="0.5" rain="0"
 *  cloud-height="150" cycle="1" seed="12345">` — scene-wide weather baseline.
 *  `seed` (optional) makes cloud/rain placement deterministic across reloads.
 */
export const weatherRecipe: Recipe = {
  name: 'Weather',
  components: ['weather'],
  parserAttributes: ['wind', 'seed'],
};

/** `wind="x z"` — direction (normalized by the runtime). */
const windAdapter: Adapter = (entity, value) => {
  const parts = String(value).trim().split(/\s+/).map(Number);
  if (parts.length >= 2 && parts.every((n) => !Number.isNaN(n))) {
    WeatherComponent.windDirX[entity] = parts[0]!;
    WeatherComponent.windDirZ[entity] = parts[1]!;
  }
};

export const WeatherPlugin: Plugin = {
  systems: [WeatherSystem],
  recipes: [weatherRecipe],
  components: {
    weather: WeatherComponent,
  },
  config: {
    defaults: {
      weather: {
        windDirX: 1,
        windDirZ: 0,
        windStrength: 1.2,
        clouds: 0.4,
        cloudHeight: 150,
        rain: 0,
        cycle: 1,
        seed: 0,
        seeded: 0,
      },
    },
    adapters: {
      weather: {
        wind: windAdapter,
      },
    },
  },
};
