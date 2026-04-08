import { beforeEach, describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import { State, XMLParser, parseXMLToEntities } from 'vibegame';
import { toCamelCase, toKebabCase } from 'vibegame';
import { PostprocessingPlugin } from '../../../src/plugins/postprocessing/plugin';
import {
  Vignette,
  DepthOfField,
  ChromaticAberration,
  Noise,
  Bloom,
  Tonemapping,
  SMAA,
  Dithering,
} from '../../../src/plugins/postprocessing/components';

describe('Postprocessing XML Parsing', () => {
  beforeEach(() => {
    const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
    global.DOMParser = dom.window.DOMParser;
  });

  describe('property string parsing ("key: value; key: value")', () => {
    it('should parse vignette attribute with two properties', () => {
      const state = new State();
      state.registerPlugin(PostprocessingPlugin);

      const xml =
        '<root><entity vignette="darkness: 0.8; offset: 0.15"></entity></root>';
      const parsed = XMLParser.parse(xml);
      const entities = parseXMLToEntities(state, parsed.root);

      const entity = entities[0].entity;
      expect(Vignette.darkness[entity]).toBeCloseTo(0.8);
      expect(Vignette.offset[entity]).toBeCloseTo(0.15);
    });

    it('should parse depth-of-field with multiple properties', () => {
      const state = new State();
      state.registerPlugin(PostprocessingPlugin);

      const xml =
        '<root><entity depth-of-field="focus-distance: 25; focal-length: 0.1; bokeh-scale: 3; auto-focus: off"></entity></root>';
      const parsed = XMLParser.parse(xml);
      const entities = parseXMLToEntities(state, parsed.root);

      const entity = entities[0].entity;
      expect(DepthOfField.focusDistance[entity]).toBeCloseTo(25);
      expect(DepthOfField.focalLength[entity]).toBeCloseTo(0.1);
      expect(DepthOfField.bokehScale[entity]).toBeCloseTo(3);
      expect(DepthOfField.autoFocus[entity]).toBe(0);
    });

    it('should parse chromatic-aberration with offset-x and offset-y', () => {
      const state = new State();
      state.registerPlugin(PostprocessingPlugin);

      const xml =
        '<root><entity chromatic-aberration="offset-x: 0.005; offset-y: 0.003; radial-modulation: on; modulation-offset: 0.2"></entity></root>';
      const parsed = XMLParser.parse(xml);
      const entities = parseXMLToEntities(state, parsed.root);

      const entity = entities[0].entity;
      expect(ChromaticAberration.offsetX[entity]).toBeCloseTo(0.005);
      expect(ChromaticAberration.offsetY[entity]).toBeCloseTo(0.003);
      expect(ChromaticAberration.radialModulation[entity]).toBe(1);
      expect(ChromaticAberration.modulationOffset[entity]).toBeCloseTo(0.2);
    });

    it('should parse noise with blend-function enum', () => {
      const state = new State();
      state.registerPlugin(PostprocessingPlugin);

      const xml =
        '<root><entity noise="opacity: 0.4; blend-function: overlay"></entity></root>';
      const parsed = XMLParser.parse(xml);
      const entities = parseXMLToEntities(state, parsed.root);

      const entity = entities[0].entity;
      expect(Noise.opacity[entity]).toBeCloseTo(0.4);
      expect(Noise.blendFunction[entity]).toBe(6);
    });
  });

  describe('kebab-case to camelCase conversion', () => {
    it('should convert kebab-case component attribute to camelCase field', () => {
      expect(toCamelCase('focus-distance')).toBe('focusDistance');
      expect(toCamelCase('focal-length')).toBe('focalLength');
      expect(toCamelCase('bokeh-scale')).toBe('bokehScale');
      expect(toCamelCase('resolution-scale')).toBe('resolutionScale');
      expect(toCamelCase('auto-focus')).toBe('autoFocus');
    });

    it('should convert kebab-case component names to camelCase', () => {
      expect(toCamelCase('depth-of-field')).toBe('depthOfField');
      expect(toCamelCase('chromatic-aberration')).toBe('chromaticAberration');
      expect(toCamelCase('blend-function')).toBe('blendFunction');
      expect(toCamelCase('radial-modulation')).toBe('radialModulation');
      expect(toCamelCase('modulation-offset')).toBe('modulationOffset');
    });

    it('should convert camelCase to kebab-case', () => {
      expect(toKebabCase('depthOfField')).toBe('depth-of-field');
      expect(toKebabCase('chromaticAberration')).toBe('chromatic-aberration');
      expect(toKebabCase('blendFunction')).toBe('blend-function');
    });

    it('should handle multi-word kebab attributes in XML pipeline', () => {
      const state = new State();
      state.registerPlugin(PostprocessingPlugin);

      const xml =
        '<root><entity depth-of-field="auto-focus: on; resolution-scale: 0.75"></entity></root>';
      const parsed = XMLParser.parse(xml);
      const entities = parseXMLToEntities(state, parsed.root);

      const entity = entities[0].entity;
      expect(DepthOfField.autoFocus[entity]).toBe(1);
      expect(DepthOfField.resolutionScale[entity]).toBeCloseTo(0.75);
    });
  });

  describe('enum parsing for postprocessing components', () => {
    it('should map tonemapping mode "aces-filmic" to 7', () => {
      const state = new State();
      state.registerPlugin(PostprocessingPlugin);

      const xml =
        '<root><entity tonemapping="mode: aces-filmic"></entity></root>';
      const parsed = XMLParser.parse(xml);
      const entities = parseXMLToEntities(state, parsed.root);

      expect(Tonemapping.mode[entities[0].entity]).toBe(7);
    });

    it('should map tonemapping mode "reinhard" to 1', () => {
      const state = new State();
      state.registerPlugin(PostprocessingPlugin);

      const xml = '<root><entity tonemapping="mode: reinhard"></entity></root>';
      const parsed = XMLParser.parse(xml);
      const entities = parseXMLToEntities(state, parsed.root);

      expect(Tonemapping.mode[entities[0].entity]).toBe(1);
    });

    it('should map smaa preset "high" to 2', () => {
      const state = new State();
      state.registerPlugin(PostprocessingPlugin);

      const xml = '<root><entity smaa="preset: high"></entity></root>';
      const parsed = XMLParser.parse(xml);
      const entities = parseXMLToEntities(state, parsed.root);

      expect(SMAA.preset[entities[0].entity]).toBe(2);
    });

    it('should map smaa preset "low" to 0', () => {
      const state = new State();
      state.registerPlugin(PostprocessingPlugin);

      const xml = '<root><entity smaa="preset: low"></entity></root>';
      const parsed = XMLParser.parse(xml);
      const entities = parseXMLToEntities(state, parsed.root);

      expect(SMAA.preset[entities[0].entity]).toBe(0);
    });

    it('should map depth-of-field auto-focus "on" to 1 and "off" to 0', () => {
      const state = new State();
      state.registerPlugin(PostprocessingPlugin);

      const xml =
        '<root><entity depth-of-field="auto-focus: on"></entity></root>';
      const parsed = XMLParser.parse(xml);
      const entities = parseXMLToEntities(state, parsed.root);

      expect(DepthOfField.autoFocus[entities[0].entity]).toBe(1);
    });

    it('should map chromatic-aberration radial-modulation "on" to 1', () => {
      const state = new State();
      state.registerPlugin(PostprocessingPlugin);

      const xml =
        '<root><entity chromatic-aberration="radial-modulation: on"></entity></root>';
      const parsed = XMLParser.parse(xml);
      const entities = parseXMLToEntities(state, parsed.root);

      expect(ChromaticAberration.radialModulation[entities[0].entity]).toBe(1);
    });

    it('should map noise blend-function "normal" to 1', () => {
      const state = new State();
      state.registerPlugin(PostprocessingPlugin);

      const xml =
        '<root><entity noise="blend-function: normal"></entity></root>';
      const parsed = XMLParser.parse(xml);
      const entities = parseXMLToEntities(state, parsed.root);

      expect(Noise.blendFunction[entities[0].entity]).toBe(1);
    });

    it('should map noise blend-function "multiply" to 3', () => {
      const state = new State();
      state.registerPlugin(PostprocessingPlugin);

      const xml =
        '<root><entity noise="blend-function: multiply"></entity></root>';
      const parsed = XMLParser.parse(xml);
      const entities = parseXMLToEntities(state, parsed.root);

      expect(Noise.blendFunction[entities[0].entity]).toBe(3);
    });
  });

  describe('defaults and overrides', () => {
    it('should apply vignette defaults when attribute has only darkness', () => {
      const state = new State();
      state.registerPlugin(PostprocessingPlugin);

      const defaults = PostprocessingPlugin.config!.defaults!.vignette;

      const xml = '<root><entity vignette="darkness: 0.7"></entity></root>';
      const parsed = XMLParser.parse(xml);
      const entities = parseXMLToEntities(state, parsed.root);

      const entity = entities[0].entity;
      expect(Vignette.darkness[entity]).toBeCloseTo(0.7);
      expect(Vignette.offset[entity]).toBeCloseTo(defaults.offset);
    });

    it('should apply bloom defaults when attribute has only intensity', () => {
      const state = new State();
      state.registerPlugin(PostprocessingPlugin);

      const defaults = PostprocessingPlugin.config!.defaults!.bloom;

      const xml = '<root><entity bloom="intensity: 3.0"></entity></root>';
      const parsed = XMLParser.parse(xml);
      const entities = parseXMLToEntities(state, parsed.root);

      const entity = entities[0].entity;
      expect(Bloom.intensity[entity]).toBeCloseTo(3.0);
      expect(Bloom.luminanceThreshold[entity]).toBeCloseTo(
        defaults.luminanceThreshold
      );
      expect(Bloom.luminanceSmoothing[entity]).toBeCloseTo(
        defaults.luminanceSmoothing
      );
    });

    it('should apply tonemapping defaults when attribute has only mode', () => {
      const state = new State();
      state.registerPlugin(PostprocessingPlugin);

      const defaults = PostprocessingPlugin.config!.defaults!.tonemapping;

      const xml =
        '<root><entity tonemapping="mode: reinhard2"></entity></root>';
      const parsed = XMLParser.parse(xml);
      const entities = parseXMLToEntities(state, parsed.root);

      const entity = entities[0].entity;
      expect(Tonemapping.mode[entity]).toBe(2);
      expect(Tonemapping.middleGrey[entity]).toBeCloseTo(defaults.middleGrey);
      expect(Tonemapping.whitePoint[entity]).toBeCloseTo(defaults.whitePoint);
    });

    it('should apply depth-of-field defaults for unspecified fields', () => {
      const state = new State();
      state.registerPlugin(PostprocessingPlugin);

      const defaults = PostprocessingPlugin.config!.defaults!.depthOfField;

      const xml =
        '<root><entity depth-of-field="focus-distance: 15"></entity></root>';
      const parsed = XMLParser.parse(xml);
      const entities = parseXMLToEntities(state, parsed.root);

      const entity = entities[0].entity;
      expect(DepthOfField.focusDistance[entity]).toBeCloseTo(15);
      expect(DepthOfField.focalLength[entity]).toBeCloseTo(
        defaults.focalLength
      );
      expect(DepthOfField.bokehScale[entity]).toBeCloseTo(defaults.bokehScale);
    });

    it('should apply dithering defaults for unspecified fields', () => {
      const state = new State();
      state.registerPlugin(PostprocessingPlugin);

      const defaults = PostprocessingPlugin.config!.defaults!.dithering;

      const xml = '<root><entity dithering="color-bits: 8"></entity></root>';
      const parsed = XMLParser.parse(xml);
      const entities = parseXMLToEntities(state, parsed.root);

      const entity = entities[0].entity;
      expect(Dithering.colorBits[entity]).toBe(8);
      expect(Dithering.intensity[entity]).toBeCloseTo(defaults.intensity);
      expect(Dithering.scale[entity]).toBeCloseTo(defaults.scale);
    });
  });

  describe('entity with postprocessing attributes', () => {
    it('should parse entity with vignette attribute', () => {
      const state = new State();
      state.registerPlugin(PostprocessingPlugin);

      const xml =
        '<root><entity vignette="darkness: 0.6; offset: 0.2"></entity></root>';
      const parsed = XMLParser.parse(xml);
      const entities = parseXMLToEntities(state, parsed.root);

      expect(entities).toHaveLength(1);
      const entity = entities[0].entity;
      expect(state.hasComponent(entity, Vignette)).toBe(true);
      expect(Vignette.darkness[entity]).toBeCloseTo(0.6);
      expect(Vignette.offset[entity]).toBeCloseTo(0.2);
    });

    it('should parse entity with multiple postprocessing effects', () => {
      const state = new State();
      state.registerPlugin(PostprocessingPlugin);

      const xml =
        '<root><entity vignette="darkness: 0.5" bloom="intensity: 1.2" tonemapping="mode: aces-filmic"></entity></root>';
      const parsed = XMLParser.parse(xml);
      const entities = parseXMLToEntities(state, parsed.root);

      expect(entities).toHaveLength(1);
      const entity = entities[0].entity;
      expect(state.hasComponent(entity, Vignette)).toBe(true);
      expect(state.hasComponent(entity, Bloom)).toBe(true);
      expect(state.hasComponent(entity, Tonemapping)).toBe(true);
      expect(Vignette.darkness[entity]).toBeCloseTo(0.5);
      expect(Bloom.intensity[entity]).toBeCloseTo(1.2);
      expect(Tonemapping.mode[entity]).toBe(7);
    });
  });
});
