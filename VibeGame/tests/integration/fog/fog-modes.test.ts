import { beforeEach, describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import { validateRecipeAttributes } from 'vibegame/core/validation';
import { State, XMLParser, parseXMLToEntities } from 'vibegame';
import { FogPlugin } from '../../../src/plugins/fog/plugin';
import { Fog } from '../../../src/plugins/fog/components';

describe('Fog Modes Integration', () => {
  beforeEach(() => {
    const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
    global.DOMParser = dom.window.DOMParser;
  });

  describe('mode enum validation', () => {
    it('should accept "exponential" as valid mode', () => {
      const result = validateRecipeAttributes('fog', { mode: 'exponential' });
      expect(result.mode).toBe('exponential');
    });

    it('should accept "exponential-squared" as valid mode', () => {
      const result = validateRecipeAttributes('fog', { mode: 'exponential-squared' });
      expect(result.mode).toBe('exponential-squared');
    });

    it('should accept "linear" as valid mode', () => {
      const result = validateRecipeAttributes('fog', { mode: 'linear' });
      expect(result.mode).toBe('linear');
    });

    it('should reject invalid mode value', () => {
      expect(() => validateRecipeAttributes('fog', { mode: 'volumetric' })).toThrow();
    });

    it('should reject numeric mode value', () => {
      expect(() => validateRecipeAttributes('fog', { mode: '0' })).toThrow();
    });
  });

  describe('mode enum mapping through pipeline', () => {
    it('should map "exponential" → mode=0 on Fog component', () => {
      const state = new State();
      state.registerPlugin(FogPlugin);

      const xml = '<root><fog mode="exponential"></fog></root>';
      const parsed = XMLParser.parse(xml);
      const entities = parseXMLToEntities(state, parsed.root);

      const entity = entities[0].entity;
      expect(Fog.mode[entity]).toBe(0);
    });

    it('should map "exponential-squared" → mode=1 on Fog component', () => {
      const state = new State();
      state.registerPlugin(FogPlugin);

      const xml = '<root><fog mode="exponential-squared"></fog></root>';
      const parsed = XMLParser.parse(xml);
      const entities = parseXMLToEntities(state, parsed.root);

      const entity = entities[0].entity;
      expect(Fog.mode[entity]).toBe(1);
    });

    it('should map "linear" → mode=2 on Fog component', () => {
      const state = new State();
      state.registerPlugin(FogPlugin);

      const xml = '<root><fog mode="linear"></fog></root>';
      const parsed = XMLParser.parse(xml);
      const entities = parseXMLToEntities(state, parsed.root);

      const entity = entities[0].entity;
      expect(Fog.mode[entity]).toBe(2);
    });

    it('should default to mode=0 (exponential) when no mode specified', () => {
      const state = new State();
      state.registerPlugin(FogPlugin);

      const xml = '<root><fog></fog></root>';
      const parsed = XMLParser.parse(xml);
      const entities = parseXMLToEntities(state, parsed.root);

      const entity = entities[0].entity;
      expect(Fog.mode[entity]).toBe(0);
    });
  });

  describe('quality enum validation', () => {
    it('should accept "low" as valid quality', () => {
      const result = validateRecipeAttributes('fog', { quality: 'low' });
      expect(result.quality).toBe('low');
    });

    it('should accept "medium" as valid quality', () => {
      const result = validateRecipeAttributes('fog', { quality: 'medium' });
      expect(result.quality).toBe('medium');
    });

    it('should accept "high" as valid quality', () => {
      const result = validateRecipeAttributes('fog', { quality: 'high' });
      expect(result.quality).toBe('high');
    });

    it('should reject invalid quality value', () => {
      expect(() => validateRecipeAttributes('fog', { quality: 'ultra' })).toThrow();
    });
  });

  describe('quality enum mapping through pipeline', () => {
    it('should map "low" → quality=0 on Fog component', () => {
      const state = new State();
      state.registerPlugin(FogPlugin);

      const xml = '<root><fog quality="low"></fog></root>';
      const parsed = XMLParser.parse(xml);
      const entities = parseXMLToEntities(state, parsed.root);

      const entity = entities[0].entity;
      expect(Fog.quality[entity]).toBe(0);
    });

    it('should map "medium" → quality=1 on Fog component', () => {
      const state = new State();
      state.registerPlugin(FogPlugin);

      const xml = '<root><fog quality="medium"></fog></root>';
      const parsed = XMLParser.parse(xml);
      const entities = parseXMLToEntities(state, parsed.root);

      const entity = entities[0].entity;
      expect(Fog.quality[entity]).toBe(1);
    });

    it('should map "high" → quality=2 on Fog component', () => {
      const state = new State();
      state.registerPlugin(FogPlugin);

      const xml = '<root><fog quality="high"></fog></root>';
      const parsed = XMLParser.parse(xml);
      const entities = parseXMLToEntities(state, parsed.root);

      const entity = entities[0].entity;
      expect(Fog.quality[entity]).toBe(2);
    });

    it('should default to quality=1 (medium) when no quality specified', () => {
      const state = new State();
      state.registerPlugin(FogPlugin);

      const xml = '<root><fog></fog></root>';
      const parsed = XMLParser.parse(xml);
      const entities = parseXMLToEntities(state, parsed.root);

      const entity = entities[0].entity;
      expect(Fog.quality[entity]).toBe(1);
    });
  });

  describe('combined mode and quality scenarios', () => {
    it('should parse exponential fog with high quality volumetric', () => {
      const state = new State();
      state.registerPlugin(FogPlugin);

      const xml =
        '<root><fog mode="exponential" density="0.03" quality="high" volumetric-strength="0.9" noise-scale="2.0"></fog></root>';
      const parsed = XMLParser.parse(xml);
      const entities = parseXMLToEntities(state, parsed.root);

      const entity = entities[0].entity;
      expect(Fog.mode[entity]).toBe(0);
      expect(Fog.density[entity]).toBeCloseTo(0.03);
      expect(Fog.quality[entity]).toBe(2);
      expect(Fog.volumetricStrength[entity]).toBeCloseTo(0.9);
      expect(Fog.noiseScale[entity]).toBeCloseTo(2.0);
    });

    it('should parse linear fog with near/far and no volumetric', () => {
      const state = new State();
      state.registerPlugin(FogPlugin);

      const xml =
        '<root><fog mode="linear" near="10" far="200" quality="low"></fog></root>';
      const parsed = XMLParser.parse(xml);
      const entities = parseXMLToEntities(state, parsed.root);

      const entity = entities[0].entity;
      expect(Fog.mode[entity]).toBe(2);
      expect(Fog.near[entity]).toBe(10);
      expect(Fog.far[entity]).toBe(200);
      expect(Fog.quality[entity]).toBe(0);
    });

    it('should parse exponential-squared fog with custom height falloff', () => {
      const state = new State();
      state.registerPlugin(FogPlugin);

      const xml =
        '<root><fog mode="exponential-squared" density="0.05" height-falloff="3.0" base-height="-2"></fog></root>';
      const parsed = XMLParser.parse(xml);
      const entities = parseXMLToEntities(state, parsed.root);

      const entity = entities[0].entity;
      expect(Fog.mode[entity]).toBe(1);
      expect(Fog.density[entity]).toBeCloseTo(0.05);
      expect(Fog.heightFalloff[entity]).toBeCloseTo(3.0);
      expect(Fog.baseHeight[entity]).toBe(-2);
    });
  });
});
