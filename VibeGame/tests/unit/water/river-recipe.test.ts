import { beforeEach, describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import { State, XMLParser, parseXMLToEntities } from 'aigamekit-vibegame';
import { TransformsPlugin } from 'aigamekit-vibegame/transforms';
import { WaterPlugin } from '../../../src/plugins/water/plugin';
import { River, getRiverPath } from '../../../src/plugins/water/components';

describe('River recipe', () => {
  beforeEach(() => {
    const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
    global.DOMParser = dom.window.DOMParser;
  });

  it('parses <River> with a path and applies defaults', () => {
    const state = new State();
    state.registerPlugin(TransformsPlugin);
    state.registerPlugin(WaterPlugin);
    const xml = '<root><River path="0 0 100 20 200 15"></River></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);
    expect(entities).toHaveLength(1);
    const eid = entities[0].entity;
    expect(River.width[eid]).toBe(WaterPlugin.config!.defaults!.river.width);
    expect(River.depth[eid]).toBe(WaterPlugin.config!.defaults!.river.depth);
    const path = getRiverPath(state, eid);
    expect(path).toEqual([0, 0, 100, 20, 200, 15]);
  });

  it('parses width/depth/color attributes', () => {
    const state = new State();
    state.registerPlugin(TransformsPlugin);
    state.registerPlugin(WaterPlugin);
    const xml =
      '<root><River path="0 0 10 0" width="12" depth="3" color="#3a5a7a"></River></root>';
    const parsed = XMLParser.parse(xml);
    const [e] = parseXMLToEntities(state, parsed.root);
    expect(River.width[e.entity]).toBe(12);
    expect(River.depth[e.entity]).toBe(3);
    expect(River.color[e.entity]).toBe(0x3a5a7a);
  });
});
