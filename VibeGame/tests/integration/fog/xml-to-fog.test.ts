import { beforeEach, describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import { validateRecipeAttributes, validateXMLContent } from 'vibegame/core/validation';
import { State, XMLParser, parseXMLToEntities } from 'vibegame';
import { FogPlugin } from '../../../src/plugins/fog/plugin';
import { Fog } from '../../../src/plugins/fog/components';

describe('XML to Fog Component Integration', () => {
  beforeEach(() => {
    const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
    global.DOMParser = dom.window.DOMParser;
  });

  it('should parse <fog> XML attributes into Fog component fields', () => {
    const attributes = {
      mode: 'exponential',
      color: '#aabbcc',
      density: '0.02',
      near: '1',
      far: '100',
      'height-falloff': '2.5',
      'base-height': '-5',
      'volumetric-strength': '0.8',
      quality: 'high',
      'noise-scale': '1.5',
    };

    const result = validateRecipeAttributes('fog', attributes);
    expect(result.mode).toBe('exponential');
    expect(result.color).toBe(0xaabbcc);
    expect(result.density).toBeCloseTo(0.02);
    expect(result.near).toBe(1);
    expect(result.far).toBe(100);
    expect(result['height-falloff']).toBeCloseTo(2.5);
    expect(result['base-height']).toBe(-5);
    expect(result['volumetric-strength']).toBeCloseTo(0.8);
    expect(result.quality).toBe('high');
    expect(result['noise-scale']).toBeCloseTo(1.5);
  });

  it('should validate <fog> tag via validateXMLContent', () => {
    const xml = `<fog mode="exponential" color="#aabbcc" density="0.02"></fog>`;
    const result = validateXMLContent(xml);
    expect(result.success).toBe(true);
  });

  it('should coerce XML string values to numbers for numeric fields', () => {
    const attributes = {
      density: '0.02',
      near: '10',
      far: '500',
      'height-falloff': '1.5',
      'base-height': '-3',
      'volumetric-strength': '0.75',
      'noise-scale': '2.0',
    };

    const result = validateRecipeAttributes('fog', attributes);
    expect(typeof result.density).toBe('number');
    expect(result.density).toBeCloseTo(0.02);
    expect(typeof result.near).toBe('number');
    expect(result.near).toBe(10);
    expect(typeof result.far).toBe('number');
    expect(result.far).toBe(500);
    expect(typeof result['height-falloff']).toBe('number');
    expect(result['height-falloff']).toBeCloseTo(1.5);
    expect(typeof result['base-height']).toBe('number');
    expect(result['base-height']).toBe(-3);
    expect(typeof result['volumetric-strength']).toBe('number');
    expect(result['volumetric-strength']).toBeCloseTo(0.75);
    expect(typeof result['noise-scale']).toBe('number');
    expect(result['noise-scale']).toBeCloseTo(2.0);
  });

  it('should parse hex color #aabbcc to integer', () => {
    const result = validateRecipeAttributes('fog', { color: '#aabbcc' });
    expect(result.color).toBe(0xaabbcc);
  });

  it('should parse 0x-prefixed color to integer', () => {
    const result = validateRecipeAttributes('fog', { color: '0xff8800' });
    expect(result.color).toBe(0xff8800);
  });

  it('should reject invalid color format', () => {
    expect(() => validateRecipeAttributes('fog', { color: 'red' })).toThrow();
  });

  it('should reject negative density', () => {
    expect(() => validateRecipeAttributes('fog', { density: '-0.01' })).toThrow();
  });

  it('should reject negative near', () => {
    expect(() => validateRecipeAttributes('fog', { near: '-1' })).toThrow();
  });

  it('should reject negative far', () => {
    expect(() => validateRecipeAttributes('fog', { far: '-10' })).toThrow();
  });

  it('should reject negative height-falloff', () => {
    expect(() =>
      validateRecipeAttributes('fog', { 'height-falloff': '-1' })
    ).toThrow();
  });

  it('should reject volumetric-strength above 1', () => {
    expect(() =>
      validateRecipeAttributes('fog', { 'volumetric-strength': '1.5' })
    ).toThrow();
  });

  it('should reject volumetric-strength below 0', () => {
    expect(() =>
      validateRecipeAttributes('fog', { 'volumetric-strength': '-0.1' })
    ).toThrow();
  });

  it('should reject negative noise-scale', () => {
    expect(() =>
      validateRecipeAttributes('fog', { 'noise-scale': '-0.5' })
    ).toThrow();
  });

  it('should reject near >= far via refine', () => {
    expect(() =>
      validateRecipeAttributes('fog', { near: '100', far: '10' })
    ).toThrow(/far.*greater.*near/);
  });

  it('should reject near === far via refine', () => {
    expect(() =>
      validateRecipeAttributes('fog', { near: '50', far: '50' })
    ).toThrow(/far.*greater.*near/);
  });

  it('should accept valid near < far', () => {
    const result = validateRecipeAttributes('fog', { near: '10', far: '100' });
    expect(result.near).toBe(10);
    expect(result.far).toBe(100);
  });

  it('should accept near without far', () => {
    const result = validateRecipeAttributes('fog', { near: '5' });
    expect(result.near).toBe(5);
  });

  it('should accept far without near', () => {
    const result = validateRecipeAttributes('fog', { far: '200' });
    expect(result.far).toBe(200);
  });

  it('should reject unknown attributes (strict mode)', () => {
    expect(() =>
      validateRecipeAttributes('fog', { unknown: 'value' })
    ).toThrow();
  });

  it('should parse fog XML through full pipeline (schema → state → entity with Fog component)', () => {
    const state = new State();
    state.registerPlugin(FogPlugin);

    const xml = '<root><fog mode="linear" density="0.05" near="5" far="200" color="#88ccee"></fog></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    expect(entities).toHaveLength(1);
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, Fog)).toBe(true);
    expect(Fog.mode[entity]).toBe(2);
    expect(Fog.density[entity]).toBeCloseTo(0.05);
    expect(Fog.near[entity]).toBe(5);
    expect(Fog.far[entity]).toBe(200);
  });

  it('should apply plugin defaults to Fog component when using full pipeline', () => {
    const state = new State();
    state.registerPlugin(FogPlugin);

    const xml = '<root><fog></fog></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    const entity = entities[0].entity;
    const defaults = FogPlugin.config.defaults.fog;
    expect(Fog.mode[entity]).toBe(defaults.mode);
    expect(Fog.density[entity]).toBeCloseTo(defaults.density);
    expect(Fog.colorR[entity]).toBeCloseTo(defaults.colorR);
    expect(Fog.colorG[entity]).toBeCloseTo(defaults.colorG);
    expect(Fog.colorB[entity]).toBeCloseTo(defaults.colorB);
    expect(Fog.heightFalloff[entity]).toBeCloseTo(defaults.heightFalloff);
    expect(Fog.baseHeight[entity]).toBe(defaults.baseHeight);
    expect(Fog.volumetricStrength[entity]).toBeCloseTo(defaults.volumetricStrength);
    expect(Fog.quality[entity]).toBe(defaults.quality);
    expect(Fog.noiseScale[entity]).toBeCloseTo(defaults.noiseScale);
  });
});
