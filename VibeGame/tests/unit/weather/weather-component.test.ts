import { describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import { MAX_ENTITIES } from '../../../src/core/ecs/constants';
import { WeatherComponent } from '../../../src/plugins/weather/components';
import {
  WeatherPlugin,
  weatherRecipe,
} from '../../../src/plugins/weather/plugin';

const FLOAT_FIELDS = [
  'windDirX',
  'windDirZ',
  'windStrength',
  'clouds',
  'cloudHeight',
  'rain',
] as const;

describe('WeatherComponent typed arrays', () => {
  for (const field of FLOAT_FIELDS) {
    it(`${field} is Float32Array(MAX_ENTITIES)`, () => {
      expect(WeatherComponent[field]).toBeInstanceOf(Float32Array);
      expect(WeatherComponent[field].length).toBe(MAX_ENTITIES);
    });
  }

  it('cycle and seeded are Uint8Array', () => {
    expect(WeatherComponent.cycle).toBeInstanceOf(Uint8Array);
    expect(WeatherComponent.seeded).toBeInstanceOf(Uint8Array);
  });

  it('seed is Uint32Array', () => {
    expect(WeatherComponent.seed).toBeInstanceOf(Uint32Array);
  });

  for (let slot = 0; slot < 35; slot++) {
    it(`weather fields round-trip on entity ${slot}`, () => {
      WeatherComponent.windDirX[slot] = slot * 0.1;
      WeatherComponent.windDirZ[slot] = slot * 0.2;
      WeatherComponent.windStrength[slot] = slot * 0.05;
      WeatherComponent.clouds[slot] = Math.min(1, slot * 0.03);
      WeatherComponent.rain[slot] = Math.min(1, slot * 0.02);
      WeatherComponent.seed[slot] = slot + 1000;
      WeatherComponent.cycle[slot] = slot % 2;
      expect(WeatherComponent.windDirX[slot]).toBeCloseTo(slot * 0.1, 5);
      expect(WeatherComponent.seed[slot]).toBe(slot + 1000);
      for (const field of FLOAT_FIELDS) WeatherComponent[field][slot] = 0;
      WeatherComponent.seed[slot] = 0;
      WeatherComponent.cycle[slot] = 0;
    });
  }
});

describe('WeatherPlugin', () => {
  it('registers Weather recipe and system', () => {
    expect(WeatherPlugin.recipes![0]).toBe(weatherRecipe);
    expect(WeatherPlugin.systems).toHaveLength(1);
  });

  const defaults = WeatherPlugin.config!.defaults!.weather as Record<
    string,
    number
  >;
  for (const [key, value] of Object.entries(defaults)) {
    it(`default weather.${key} = ${value}`, () => {
      expect(defaults[key]).toBe(value);
    });
  }

  it('wind adapter parses "x z" into component fields', () => {
    const wind = WeatherPlugin.config!.adapters!.weather.wind;
    const entity = 3;
    const state = new State();
    wind(entity, '0.6 0.8', state);
    expect(WeatherComponent.windDirX[entity]).toBeCloseTo(0.6, 5);
    expect(WeatherComponent.windDirZ[entity]).toBeCloseTo(0.8, 5);
    WeatherComponent.windDirX[entity] = 0;
    WeatherComponent.windDirZ[entity] = 0;
  });
});
