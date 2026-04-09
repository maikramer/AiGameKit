import { beforeEach, describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import { State, XMLParser, parseXMLToEntities } from 'vibegame';
import { TransformsPlugin } from 'vibegame/transforms';
import { Water } from '../../../src/plugins/water/components';
import { WaterPlugin } from '../../../src/plugins/water/plugin';

describe('WaterPlugin smoke', () => {
  beforeEach(() => {
    const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
    global.DOMParser = dom.window.DOMParser;
  });

  it('registers water recipe and defaults', () => {
    expect(WaterPlugin.recipes?.some((r) => r.name === 'water')).toBe(true);
    const defaults = WaterPlugin.config?.defaults?.water;
    expect(defaults).toBeDefined();
    expect(defaults!.size).toBe(256);
    expect(defaults!.waterLevel).toBe(5);
  });

  it('parses bare <water> and applies defaults', () => {
    const state = new State();
    state.registerPlugin(TransformsPlugin);
    state.registerPlugin(WaterPlugin);

    const xml = '<root><water></water></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    expect(entities).toHaveLength(1);
    const entity = entities[0].entity;
    const defaults = WaterPlugin.config!.defaults!.water;

    expect(Water.size[entity]).toBe(defaults.size);
    expect(Water.waterLevel[entity]).toBe(defaults.waterLevel);
    expect(Water.opacity[entity]).toBeCloseTo(defaults.opacity);
    expect(Water.tintR[entity]).toBeCloseTo(defaults.tintR);
    expect(Water.waveSpeed[entity]).toBeCloseTo(defaults.waveSpeed);
  });
});
