import { beforeEach, describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import { validateRecipeAttributes } from 'vibegame/core/validation';
import { State, XMLParser, parseXMLToEntities } from 'vibegame';
import { FogPlugin } from '../../../src/plugins/fog/plugin';
import { Fog } from '../../../src/plugins/fog/components';

describe('Fog Defaults Integration', () => {
  beforeEach(() => {
    const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
    global.DOMParser = dom.window.DOMParser;
  });

  it('should apply all defaults when <fog/> has no attributes', () => {
    const result = validateRecipeAttributes('fog', {});
    expect(result.mode).toBeUndefined();
    expect(result.color).toBeUndefined();
    expect(result.density).toBeUndefined();
    expect(result.near).toBeUndefined();
    expect(result.far).toBeUndefined();
    expect(result['height-falloff']).toBeUndefined();
    expect(result['base-height']).toBeUndefined();
    expect(result['volumetric-strength']).toBeUndefined();
    expect(result.quality).toBeUndefined();
    expect(result['noise-scale']).toBeUndefined();
  });

  it('should fill Fog component with plugin defaults for bare <fog> tag', () => {
    const state = new State();
    state.registerPlugin(FogPlugin);

    const xml = '<root><fog></fog></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    expect(entities).toHaveLength(1);
    const entity = entities[0].entity;

    const defaults = FogPlugin.config.defaults.fog;
    expect(Fog.mode[entity]).toBe(defaults.mode);
    expect(Fog.density[entity]).toBeCloseTo(defaults.density);
    expect(Fog.near[entity]).toBe(defaults.near);
    expect(Fog.far[entity]).toBe(defaults.far);
    expect(Fog.colorR[entity]).toBeCloseTo(defaults.colorR);
    expect(Fog.colorG[entity]).toBeCloseTo(defaults.colorG);
    expect(Fog.colorB[entity]).toBeCloseTo(defaults.colorB);
    expect(Fog.heightFalloff[entity]).toBeCloseTo(defaults.heightFalloff);
    expect(Fog.baseHeight[entity]).toBe(defaults.baseHeight);
    expect(Fog.volumetricStrength[entity]).toBeCloseTo(defaults.volumetricStrength);
    expect(Fog.quality[entity]).toBe(defaults.quality);
    expect(Fog.noiseScale[entity]).toBeCloseTo(defaults.noiseScale);
  });

  it('should use self-closing <fog/> as valid empty fog element', () => {
    const state = new State();
    state.registerPlugin(FogPlugin);

    const xml = '<root><fog/></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    expect(entities).toHaveLength(1);
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, Fog)).toBe(true);
    expect(Fog.mode[entity]).toBe(0);
    expect(Fog.density[entity]).toBeCloseTo(0.015);
  });

  it('should override only specified attributes while keeping defaults', () => {
    const state = new State();
    state.registerPlugin(FogPlugin);

    const xml = '<root><fog density="0.1" quality="high"></fog></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    const entity = entities[0].entity;
    const defaults = FogPlugin.config.defaults.fog;

    expect(Fog.density[entity]).toBeCloseTo(0.1);
    expect(Fog.quality[entity]).toBe(2);

    expect(Fog.mode[entity]).toBe(defaults.mode);
    expect(Fog.colorR[entity]).toBeCloseTo(defaults.colorR);
    expect(Fog.colorG[entity]).toBeCloseTo(defaults.colorG);
    expect(Fog.colorB[entity]).toBeCloseTo(defaults.colorB);
    expect(Fog.heightFalloff[entity]).toBeCloseTo(defaults.heightFalloff);
    expect(Fog.baseHeight[entity]).toBe(defaults.baseHeight);
    expect(Fog.volumetricStrength[entity]).toBeCloseTo(defaults.volumetricStrength);
    expect(Fog.noiseScale[entity]).toBeCloseTo(defaults.noiseScale);
  });

  it('should use near/far defaults from plugin (near=1, far=1000)', () => {
    const state = new State();
    state.registerPlugin(FogPlugin);

    const xml = '<root><fog></fog></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    const entity = entities[0].entity;
    expect(Fog.near[entity]).toBe(1);
    expect(Fog.far[entity]).toBe(1000);
  });

  it('should use default color RGB values (0.533, 0.6, 0.667)', () => {
    const state = new State();
    state.registerPlugin(FogPlugin);

    const xml = '<root><fog></fog></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    const entity = entities[0].entity;
    expect(Fog.colorR[entity]).toBeCloseTo(0.533);
    expect(Fog.colorG[entity]).toBeCloseTo(0.6);
    expect(Fog.colorB[entity]).toBeCloseTo(0.667);
  });

  it('should use default volumetric-strength=0.5', () => {
    const state = new State();
    state.registerPlugin(FogPlugin);

    const xml = '<root><fog></fog></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    const entity = entities[0].entity;
    expect(Fog.volumetricStrength[entity]).toBeCloseTo(0.5);
  });

  it('should use default noise-scale=1.0', () => {
    const state = new State();
    state.registerPlugin(FogPlugin);

    const xml = '<root><fog></fog></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    const entity = entities[0].entity;
    expect(Fog.noiseScale[entity]).toBeCloseTo(1.0);
  });

  it('should use default quality=1 (medium)', () => {
    const state = new State();
    state.registerPlugin(FogPlugin);

    const xml = '<root><fog></fog></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    const entity = entities[0].entity;
    expect(Fog.quality[entity]).toBe(1);
  });
});
