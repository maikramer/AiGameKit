import { beforeEach, describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import { State, XMLParser, parseXMLToEntities, toKebabCase } from 'vibegame';
import { PostprocessingPlugin } from '../../../src/plugins/postprocessing/plugin';
import {
  getEffectDefinitions,
  unregisterEffect,
} from '../../../src/plugins/postprocessing/effect-registry';
import { registerBuiltinEffects } from '../../../src/plugins/postprocessing/builtin-effects';
import {
  Bloom,
  SMAA,
  Dithering,
  Tonemapping,
  Vignette,
  DepthOfField,
  ChromaticAberration,
  Noise,
  ScreenSpaceAmbientOcclusion,
  ScreenSpaceReflection,
} from '../../../src/plugins/postprocessing/components';

const EXPECTED_EFFECT_KEYS = [
  'smaa',
  'bloom',
  'dithering',
  'tonemapping',
  'vignette',
  'depthOfField',
  'chromaticAberration',
  'noise',
  'ssao',
  'ssr',
] as const;

const EFFECT_COMPONENTS = {
  smaa: SMAA,
  bloom: Bloom,
  dithering: Dithering,
  tonemapping: Tonemapping,
  vignette: Vignette,
  depthOfField: DepthOfField,
  chromaticAberration: ChromaticAberration,
  noise: Noise,
  ssao: ScreenSpaceAmbientOcclusion,
  ssr: ScreenSpaceReflection,
} as const;

describe('Postprocessing Effect Registration', () => {
  beforeEach(() => {
    const dom = new JSDOM('<!DOCTYPE html><html><body></html>');
    global.DOMParser = dom.window.DOMParser;
  });

  it('should have exactly 10 registered effects', () => {
    const definitions = getEffectDefinitions();
    expect(definitions).toHaveLength(10);
  });

  it('should have all 10 expected effect keys registered', () => {
    const definitions = getEffectDefinitions();
    const keys = definitions.map((d) => d.key);
    for (const expectedKey of EXPECTED_EFFECT_KEYS) {
      expect(keys).toContain(expectedKey);
    }
  });

  it('should register SMAA with position "first"', () => {
    const definitions = getEffectDefinitions();
    const smaa = definitions.find((d) => d.key === 'smaa');
    expect(smaa).toBeDefined();
    expect(smaa!.position).toBe('first');
  });

  it('should register tonemapping with position "last"', () => {
    const definitions = getEffectDefinitions();
    const tm = definitions.find((d) => d.key === 'tonemapping');
    expect(tm).toBeDefined();
    expect(tm!.position).toBe('last');
  });

  it('should register effects with correct component references', () => {
    const definitions = getEffectDefinitions();
    for (const [key, component] of Object.entries(EFFECT_COMPONENTS)) {
      const def = definitions.find((d) => d.key === key);
      expect(def).toBeDefined();
      expect(def!.component).toBe(component);
    }
  });

  it('should have create function on every effect definition', () => {
    const definitions = getEffectDefinitions();
    for (const def of definitions) {
      expect(typeof def.create).toBe('function');
    }
  });

  it('should have update function on all effects except smaa', () => {
    const definitions = getEffectDefinitions();
    const withoutUpdate = definitions.filter((d) => !d.update);
    expect(withoutUpdate).toHaveLength(1);
    expect(withoutUpdate[0].key).toBe('smaa');
  });

  it('should have position undefined on middle effects (not first/last)', () => {
    const definitions = getEffectDefinitions();
    const middleKeys = [
      'bloom',
      'dithering',
      'vignette',
      'depthOfField',
      'chromaticAberration',
      'noise',
      'ssao',
      'ssr',
    ];
    for (const key of middleKeys) {
      const def = definitions.find((d) => d.key === key);
      expect(def).toBeDefined();
      expect(def!.position).toBeUndefined();
    }
  });

  it('should allow unregistering an effect', () => {
    const defsBefore = getEffectDefinitions();
    const countBefore = defsBefore.length;

    const removed = unregisterEffect('noise');
    expect(removed).toBe(true);
    expect(getEffectDefinitions()).toHaveLength(countBefore - 1);

    const keys = getEffectDefinitions().map((d) => d.key);
    expect(keys).not.toContain('noise');

    registerBuiltinEffects();
  });

  it('should return false when unregistering a non-existent effect', () => {
    const removed = unregisterEffect('nonexistent-effect');
    expect(removed).toBe(false);
  });

  it('should register PostprocessingPlugin and make components available', () => {
    const state = new State();
    state.registerPlugin(PostprocessingPlugin);

    for (const [key, component] of Object.entries(EFFECT_COMPONENTS)) {
      const registered = state.getComponent(toKebabCase(key));
      expect(registered).toBe(component);
    }
  });

  it('should add Vignette component to entity with vignette attribute', () => {
    const state = new State();
    state.registerPlugin(PostprocessingPlugin);

    const xml =
      '<root><GameObject vignette="darkness: 0.8; offset: 0.15"></GameObject></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    expect(entities).toHaveLength(1);
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, Vignette)).toBe(true);
    expect(Vignette.darkness[entity]).toBeCloseTo(0.8);
    expect(Vignette.offset[entity]).toBeCloseTo(0.15);
  });

  it('should add Noise component to entity with noise attribute', () => {
    const state = new State();
    state.registerPlugin(PostprocessingPlugin);

    const xml =
      '<root><GameObject noise="opacity: 0.3; blend-function: skip"></GameObject></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    expect(entities).toHaveLength(1);
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, Noise)).toBe(true);
    expect(Noise.opacity[entity]).toBeCloseTo(0.3);
    expect(Noise.blendFunction[entity]).toBe(0);
  });

  it('should add Bloom component to entity with bloom attribute', () => {
    const state = new State();
    state.registerPlugin(PostprocessingPlugin);

    const xml =
      '<root><GameObject bloom="intensity: 2.0; luminance-threshold: 0.8"></GameObject></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    expect(entities).toHaveLength(1);
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, Bloom)).toBe(true);
    expect(Bloom.intensity[entity]).toBeCloseTo(2.0);
    expect(Bloom.luminanceThreshold[entity]).toBeCloseTo(0.8);
  });

  it('should add ChromaticAberration component to entity', () => {
    const state = new State();
    state.registerPlugin(PostprocessingPlugin);

    const xml =
      '<root><GameObject chromatic-aberration="offset-x: 0.003; offset-y: 0.002"></GameObject></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    expect(entities).toHaveLength(1);
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, ChromaticAberration)).toBe(true);
    expect(ChromaticAberration.offsetX[entity]).toBeCloseTo(0.003);
    expect(ChromaticAberration.offsetY[entity]).toBeCloseTo(0.002);
  });

  it('should add DepthOfField component to entity with defaults', () => {
    const state = new State();
    state.registerPlugin(PostprocessingPlugin);

    const xml =
      '<root><GameObject depth-of-field="focus-distance: 20; focal-length: 0.08"></GameObject></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    expect(entities).toHaveLength(1);
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, DepthOfField)).toBe(true);
    expect(DepthOfField.focusDistance[entity]).toBeCloseTo(20);
    expect(DepthOfField.focalLength[entity]).toBeCloseTo(0.08);
  });

  it('should add Tonemapping component with enum mode', () => {
    const state = new State();
    state.registerPlugin(PostprocessingPlugin);

    const xml =
      '<root><GameObject tonemapping="mode: aces-filmic; middle-grey: 0.5"></GameObject></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    expect(entities).toHaveLength(1);
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, Tonemapping)).toBe(true);
    expect(Tonemapping.mode[entity]).toBe(7);
    expect(Tonemapping.middleGrey[entity]).toBeCloseTo(0.5);
  });

  it('should add SMAA component with enum preset', () => {
    const state = new State();
    state.registerPlugin(PostprocessingPlugin);

    const xml = '<root><GameObject smaa="preset: ultra"></GameObject></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    expect(entities).toHaveLength(1);
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, SMAA)).toBe(true);
    expect(SMAA.preset[entity]).toBe(3);
  });

  it('should add Dithering component to entity', () => {
    const state = new State();
    state.registerPlugin(PostprocessingPlugin);

    const xml =
      '<root><GameObject dithering="color-bits: 4; intensity: 1.0"></GameObject></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    expect(entities).toHaveLength(1);
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, Dithering)).toBe(true);
    expect(Dithering.colorBits[entity]).toBe(4);
    expect(Dithering.intensity[entity]).toBeCloseTo(1.0);
  });

  it('should apply plugin defaults to Vignette when only some attributes specified', () => {
    const state = new State();
    state.registerPlugin(PostprocessingPlugin);

    const defaults = PostprocessingPlugin.config!.defaults!.vignette;

    const xml = '<root><GameObject vignette="darkness: 0.9"></GameObject></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    const entity = entities[0].entity;
    expect(Vignette.darkness[entity]).toBeCloseTo(0.9);
    expect(Vignette.offset[entity]).toBeCloseTo(defaults.offset);
  });

  it('should apply all defaults to Noise when no attributes specified', () => {
    const state = new State();
    state.registerPlugin(PostprocessingPlugin);

    const defaults = PostprocessingPlugin.config!.defaults!.noise;

    const xml = '<root><GameObject noise="opacity: 0.5"></GameObject></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    const entity = entities[0].entity;
    expect(Noise.opacity[entity]).toBeCloseTo(0.5);
    expect(Noise.blendFunction[entity]).toBe(defaults.blendFunction);
  });

  it('should support multiple postprocessing components on a single entity', () => {
    const state = new State();
    state.registerPlugin(PostprocessingPlugin);

    const xml =
      '<root><GameObject vignette="darkness: 0.8" bloom="intensity: 1.5" noise="opacity: 0.2"></GameObject></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    expect(entities).toHaveLength(1);
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, Vignette)).toBe(true);
    expect(state.hasComponent(entity, Bloom)).toBe(true);
    expect(state.hasComponent(entity, Noise)).toBe(true);
    expect(Vignette.darkness[entity]).toBeCloseTo(0.8);
    expect(Bloom.intensity[entity]).toBeCloseTo(1.5);
    expect(Noise.opacity[entity]).toBeCloseTo(0.2);
  });
});
