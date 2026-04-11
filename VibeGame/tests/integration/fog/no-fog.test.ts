import { beforeEach, describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import { State, XMLParser, parseXMLToEntities, defineQuery } from 'vibegame';
import { FogPlugin } from '../../../src/plugins/fog/plugin';
import { Fog } from '../../../src/plugins/fog/components';

describe('No Fog Integration', () => {
  beforeEach(() => {
    const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
    global.DOMParser = dom.window.DOMParser;
  });

  it('should create no Fog entity when <Fog> tag is absent', () => {
    const state = new State();
    state.registerPlugin(FogPlugin);

    const xml = '<root><GameObject></GameObject></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    expect(entities).toHaveLength(1);
    const entity = entities[0].entity;

    expect(state.hasComponent(entity, Fog)).toBe(false);
  });

  it('should return no entities from Fog query when no <Fog> tag present', () => {
    const state = new State();
    state.registerPlugin(FogPlugin);

    const xml = '<root><GameObject></GameObject></root>';
    const parsed = XMLParser.parse(xml);
    parseXMLToEntities(state, parsed.root);

    const fogQuery = defineQuery([Fog]);
    const fogEntities = fogQuery(state.world);
    expect(fogEntities.length).toBe(0);
  });

  it('should not create Fog entity from world-only XML', () => {
    const state = new State();
    state.registerPlugin(FogPlugin);

    const xml = '<root><GameObject></GameObject><GameObject></GameObject></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    expect(entities).toHaveLength(2);

    const fogQuery = defineQuery([Fog]);
    const fogEntities = fogQuery(state.world);
    expect(fogEntities.length).toBe(0);

    for (const result of entities) {
      expect(state.hasComponent(result.entity, Fog)).toBe(false);
    }
  });

  it('should create exactly one Fog entity when <Fog> tag is present', () => {
    const state = new State();
    state.registerPlugin(FogPlugin);

    const xml = '<root><Fog></Fog><GameObject></GameObject></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    expect(entities).toHaveLength(2);

    const fogQuery = defineQuery([Fog]);
    const fogEntities = fogQuery(state.world);
    expect(fogEntities.length).toBe(1);
  });

  it('should not create Fog entity from entities with fog-like attributes', () => {
    const state = new State();
    state.registerPlugin(FogPlugin);

    const xml = '<root><GameObject density="0.5" mode="1"></GameObject></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    const entity = entities[0].entity;
    expect(state.hasComponent(entity, Fog)).toBe(false);

    const fogQuery = defineQuery([Fog]);
    const fogEntities = fogQuery(state.world);
    expect(fogEntities.length).toBe(0);
  });

  it('should distinguish Fog entity from other entity types', () => {
    const state = new State();
    state.registerPlugin(FogPlugin);

    const xml = '<root><Fog density="0.02"></Fog><GameObject></GameObject></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    const fogQuery = defineQuery([Fog]);
    const fogEntities = fogQuery(state.world);

    expect(fogEntities.length).toBe(1);
    const fogEntity = fogEntities[0];
    expect(Fog.density[fogEntity]).toBeCloseTo(0.02);

    const nonFogEntities = entities.filter(
      (r) => !state.hasComponent(r.entity, Fog)
    );
    expect(nonFogEntities.length).toBe(1);
  });
});
