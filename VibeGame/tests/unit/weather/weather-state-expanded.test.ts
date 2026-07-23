import { describe, expect, it } from 'bun:test';
import { State } from '../../../src/core/ecs/state';
import {
  effectiveRainTarget,
  getWeather,
  getWindVector,
  setEnvironmentRain,
  setWeather,
} from '../../../src/plugins/weather/state';

describe('getWeather defaults', () => {
  for (let i = 0; i < 20; i++) {
    it(`fresh state ${i} starts with normalized wind and zero rain`, () => {
      const state = new State();
      const w = getWeather(state);
      expect(w.windDirX).toBeCloseTo(1, 5);
      expect(w.windDirZ).toBeCloseTo(0, 5);
      expect(w.rain).toBe(0);
      expect(w.cloudsTarget).toBe(0);
      expect(w.fadeSeconds).toBe(4);
    });
  }
});

describe('setWeather patches', () => {
  for (let strength = 0; strength <= 10; strength++) {
    it(`windStrength ${strength} scales getWindVector`, () => {
      const state = new State();
      setWeather(state, { windDirX: 1, windDirZ: 0, windStrength: strength });
      const v = getWindVector(state);
      expect(v.x).toBeCloseTo(strength, 5);
      expect(v.z).toBeCloseTo(0, 5);
    });
  }

  for (const [x, z] of [
    [3, 4],
    [0, 5],
    [-2, 2],
    [10, 0],
  ] as const) {
    it(`normalizes wind direction (${x}, ${z})`, () => {
      const state = new State();
      setWeather(state, { windDirX: x, windDirZ: z, windStrength: 1 });
      const w = getWeather(state);
      expect(Math.hypot(w.windDirX, w.windDirZ)).toBeCloseTo(1, 5);
    });
  }

  for (let clouds = 0; clouds <= 10; clouds++) {
    it(`clouds target ${clouds / 10} stored on cloudsTarget`, () => {
      const state = new State();
      setWeather(state, { clouds: clouds / 10 });
      expect(getWeather(state).cloudsTarget).toBeCloseTo(clouds / 10, 5);
    });
  }

  for (let rain = 0; rain <= 10; rain++) {
    it(`effectiveRainTarget picks max(api, biome) for rain=${rain / 10}`, () => {
      const state = new State();
      setWeather(state, { rain: rain / 10 });
      setEnvironmentRain(state, 0.25);
      const w = getWeather(state);
      expect(effectiveRainTarget(w)).toBeCloseTo(Math.max(rain / 10, 0.25), 5);
    });
  }
});

describe('setEnvironmentRain clamp', () => {
  for (let v = -5; v <= 5; v++) {
    it(`clamps environment rain input ${v}`, () => {
      const state = new State();
      setEnvironmentRain(state, v);
      const clamped = getWeather(state).environmentRain;
      expect(clamped).toBeGreaterThanOrEqual(0);
      expect(clamped).toBeLessThanOrEqual(1);
    });
  }
});
