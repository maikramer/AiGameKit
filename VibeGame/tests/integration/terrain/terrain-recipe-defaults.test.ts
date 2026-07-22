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

  it('parses bare <Terrain> and applies plugin defaults', () => {
    const state = new State();
    state.registerPlugin(TransformsPlugin);
    state.registerPlugin(TerrainPlugin);

    const xml = '<root><Terrain></Terrain></root>';
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
    expect(Terrain.noiseSandStrength[entity]).toBeCloseTo(
      defaults.noiseSandStrength
    );
    expect(Terrain.noiseSandScale[entity]).toBeCloseTo(defaults.noiseSandScale);
    expect(Terrain.noiseSandThreshold[entity]).toBeCloseTo(
      defaults.noiseSandThreshold
    );
    expect(Terrain.noiseSandHeightMin[entity]).toBeCloseTo(
      defaults.noiseSandHeightMin
    );
    expect(Terrain.noiseSandHeightMax[entity]).toBeCloseTo(
      defaults.noiseSandHeightMax
    );
  });

  it('parses noise-sand-* XML attrs onto Terrain', () => {
    const state = new State();
    state.registerPlugin(TransformsPlugin);
    state.registerPlugin(TerrainPlugin);

    const xml = `
      <root>
        <Terrain
          noise-sand-strength="0.7"
          noise-sand-scale="0.02"
          noise-sand-threshold="0.4"
          noise-sand-height-min="0.1"
          noise-sand-height-max="0.6"
        ></Terrain>
      </root>`;
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);
    const entity = entities[0].entity;

    expect(Terrain.noiseSandStrength[entity]).toBeCloseTo(0.7);
    expect(Terrain.noiseSandScale[entity]).toBeCloseTo(0.02);
    expect(Terrain.noiseSandThreshold[entity]).toBeCloseTo(0.4);
    expect(Terrain.noiseSandHeightMin[entity]).toBeCloseTo(0.1);
    expect(Terrain.noiseSandHeightMax[entity]).toBeCloseTo(0.6);
  });
});
