import { beforeEach, describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import { State, XMLParser, parseXMLToEntities } from 'vibegame';
import { TransformsPlugin } from 'vibegame/transforms';
import { Terrain } from '../../../src/plugins/terrain/components';
import { TerrainPlugin } from '../../../src/plugins/terrain/plugin';

describe('Terrain recipe integration', () => {
  beforeEach(() => {
    const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
    global.DOMParser = dom.window.DOMParser;
  });

  it('parses bare <terrain> and applies plugin defaults', () => {
    const state = new State();
    state.registerPlugin(TransformsPlugin);
    state.registerPlugin(TerrainPlugin);

    const xml = '<root><terrain></terrain></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    expect(entities).toHaveLength(1);
    const entity = entities[0].entity;
    const defaults = TerrainPlugin.config!.defaults!.terrain;

    expect(Terrain.worldSize[entity]).toBe(defaults.worldSize);
    expect(Terrain.maxHeight[entity]).toBe(defaults.maxHeight);
    expect(Terrain.levels[entity]).toBe(defaults.levels);
    expect(Terrain.resolution[entity]).toBe(defaults.resolution);
    expect(Terrain.roughness[entity]).toBeCloseTo(defaults.roughness);
    expect(Terrain.collisionResolution[entity]).toBe(
      defaults.collisionResolution
    );
    expect(Terrain.skirtWidth[entity]).toBeCloseTo(defaults.skirtWidth);
    expect(Terrain.baseColor[entity]).toBe(defaults.baseColor);
  });
});
