import { describe, expect, it } from 'bun:test';
import { State } from '../../../src/core/ecs/state';
import {
  effectiveCloudsTarget,
  effectiveRainTarget,
  getWeather,
  getWindVector,
  setEnvironmentClouds,
  setEnvironmentRain,
  setWeather,
} from '../../../src/plugins/weather/state';

describe('weather runtime state', () => {
  it('normalizes the wind direction and exposes the wind vector', () => {
    const state = new State();
    setWeather(state, { windDirX: 3, windDirZ: 4, windStrength: 2 });
    const w = getWeather(state);
    expect(Math.hypot(w.windDirX, w.windDirZ)).toBeCloseTo(1, 5);
    const v = getWindVector(state);
    expect(v.x).toBeCloseTo(1.2, 5); // (3/5)·2
    expect(v.z).toBeCloseTo(1.6, 5); // (4/5)·2
  });

  it('rain composes: the wetter of API target vs biome rain wins', () => {
    const state = new State();
    setWeather(state, { rain: 0.2 });
    setEnvironmentRain(state, 0.7);
    expect(effectiveRainTarget(getWeather(state))).toBeCloseTo(0.7, 5);

    setWeather(state, { rain: 0.9 });
    expect(effectiveRainTarget(getWeather(state))).toBeCloseTo(0.9, 5);

    // leaving the biome drops env rain; the API storm keeps raining
    setEnvironmentRain(state, 0);
    expect(effectiveRainTarget(getWeather(state))).toBeCloseTo(0.9, 5);
  });

  it('clamps environment rain to 0..1', () => {
    const state = new State();
    setEnvironmentRain(state, 4);
    expect(getWeather(state).environmentRain).toBe(1);
    setEnvironmentRain(state, -2);
    expect(getWeather(state).environmentRain).toBe(0);
  });

  it('biome clouds override cycle target; −1 clears override', () => {
    const state = new State();
    setWeather(state, { clouds: 0.5 });
    expect(effectiveCloudsTarget(getWeather(state))).toBeCloseTo(0.5, 5);
    setEnvironmentClouds(state, 0.85);
    expect(effectiveCloudsTarget(getWeather(state))).toBeCloseTo(0.85, 5);
    setEnvironmentClouds(state, -1);
    expect(getWeather(state).environmentClouds).toBe(-1);
    expect(effectiveCloudsTarget(getWeather(state))).toBeCloseTo(0.5, 5);
  });
});

describe('<BiomeRegion> weather/pp attrs', () => {
  it('parses pp overrides + rain into the component', async () => {
    // Load rendering before biomes: biomes participates in a pre-existing
    // module cycle that TDZ-crashes when biomes/plugin is the first entry
    // point (fine in the app, where rendering always loads first).
    await import('../../../src/plugins/rendering/plugin');
    const { createEntityFromRecipe } =
      await import('../../../src/core/recipes/parser');
    const { BiomesPlugin } = await import('../../../src/plugins/biomes/plugin');
    const { BiomeRegion } =
      await import('../../../src/plugins/biomes/components');
    const state = new State();
    state.registerPlugin(BiomesPlugin);
    const eid = createEntityFromRecipe(state, 'BiomeRegion', {
      id: 'test-swamp',
      type: '3',
      polygon: '[-10,-10;10,-10;10,10;-10,10]',
      rain: '0.55',
      clouds: '0.85',
      'pp-exposure': '0.9',
      'pp-bloom-strength': '0.25',
      'pp-vignette-darkness': '0.8',
    });
    expect(BiomeRegion.rain[eid]).toBeCloseTo(0.55, 5);
    expect(BiomeRegion.clouds[eid]).toBeCloseTo(0.85, 5);
    expect(BiomeRegion.ppExposure[eid]).toBeCloseTo(0.9, 5);
    expect(BiomeRegion.ppBloomStrength[eid]).toBeCloseTo(0.25, 5);
    expect(BiomeRegion.ppVignetteDarkness[eid]).toBeCloseTo(0.8, 5);
  });

  it('defaults clouds to −1 (no override) when omitted', async () => {
    await import('../../../src/plugins/rendering/plugin');
    const { createEntityFromRecipe } =
      await import('../../../src/core/recipes/parser');
    const { BiomesPlugin } = await import('../../../src/plugins/biomes/plugin');
    const { BiomeRegion } =
      await import('../../../src/plugins/biomes/components');
    const state = new State();
    state.registerPlugin(BiomesPlugin);
    const eid = createEntityFromRecipe(state, 'BiomeRegion', {
      id: 'test-default-clouds',
      type: '1',
      polygon: '[-5,-5;5,-5;5,5;-5,5]',
    });
    expect(BiomeRegion.clouds[eid]).toBe(-1);
  });
});
