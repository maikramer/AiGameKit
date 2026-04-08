import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import { PostprocessingPlugin } from '../../../src/plugins/postprocessing/plugin';
import {
  Vignette,
  DepthOfField,
  ChromaticAberration,
  Noise,
  Bloom,
} from '../../../src/plugins/postprocessing/components';

describe('PostprocessingPlugin Registration', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.registerPlugin(PostprocessingPlugin);
  });

  it('should export all new components from the plugin', () => {
    const components = PostprocessingPlugin.components!;
    expect(components.Vignette).toBeDefined();
    expect(components.DepthOfField).toBeDefined();
    expect(components.ChromaticAberration).toBeDefined();
    expect(components.Noise).toBeDefined();
    expect(components.Bloom).toBeDefined();
  });

  it('should export all components from index', async () => {
    const mod = await import('../../../src/plugins/postprocessing');
    expect(mod.Vignette).toBeDefined();
    expect(mod.DepthOfField).toBeDefined();
    expect(mod.ChromaticAberration).toBeDefined();
    expect(mod.Noise).toBeDefined();
    expect(mod.Bloom).toBeDefined();
    expect(mod.PostprocessingPlugin).toBeDefined();
  });

  it('should register Vignette component and allow addComponent', () => {
    const entity = state.createEntity();
    state.addComponent(entity, Vignette);
    expect(state.hasComponent(entity, Vignette)).toBe(true);
  });

  it('should register DepthOfField component and allow addComponent', () => {
    const entity = state.createEntity();
    state.addComponent(entity, DepthOfField);
    expect(state.hasComponent(entity, DepthOfField)).toBe(true);
  });

  it('should register ChromaticAberration component and allow addComponent', () => {
    const entity = state.createEntity();
    state.addComponent(entity, ChromaticAberration);
    expect(state.hasComponent(entity, ChromaticAberration)).toBe(true);
  });

  it('should register Noise component and allow addComponent', () => {
    const entity = state.createEntity();
    state.addComponent(entity, Noise);
    expect(state.hasComponent(entity, Noise)).toBe(true);
  });

  it('should register Bloom component and allow addComponent', () => {
    const entity = state.createEntity();
    state.addComponent(entity, Bloom);
    expect(state.hasComponent(entity, Bloom)).toBe(true);
  });

  it('should have config.defaults for vignette', () => {
    const defaults = PostprocessingPlugin.config!.defaults!.vignette;
    expect(defaults).toBeDefined();
    expect(defaults.darkness).toBeCloseTo(0.5);
    expect(defaults.offset).toBeCloseTo(0.1);
  });

  it('should have config.defaults for depthOfField', () => {
    const defaults = PostprocessingPlugin.config!.defaults!.depthOfField;
    expect(defaults).toBeDefined();
    expect(defaults.focusDistance).toBeCloseTo(10);
    expect(defaults.focalLength).toBeCloseTo(0.05);
    expect(defaults.bokehScale).toBe(1);
    expect(defaults.resolutionScale).toBeCloseTo(0.5);
    expect(defaults.autoFocus).toBe(1);
  });

  it('should have config.defaults for chromaticAberration', () => {
    const defaults = PostprocessingPlugin.config!.defaults!.chromaticAberration;
    expect(defaults).toBeDefined();
    expect(defaults.offsetX).toBeCloseTo(0.002);
    expect(defaults.offsetY).toBeCloseTo(0.001);
    expect(defaults.radialModulation).toBe(0);
    expect(defaults.modulationOffset).toBeCloseTo(0.15);
  });

  it('should have config.defaults for noise', () => {
    const defaults = PostprocessingPlugin.config!.defaults!.noise;
    expect(defaults).toBeDefined();
    expect(defaults.opacity).toBeCloseTo(0.2);
    expect(defaults.blendFunction).toBe(0);
  });

  it('should have config.defaults for bloom including luminanceSmoothing', () => {
    const defaults = PostprocessingPlugin.config!.defaults!.bloom;
    expect(defaults).toBeDefined();
    expect(defaults.intensity).toBeCloseTo(1.0);
    expect(defaults.luminanceThreshold).toBeCloseTo(1.0);
    expect(defaults.luminanceSmoothing).toBeCloseTo(0.3);
    expect(defaults.mipmapBlur).toBe(1);
    expect(defaults.radius).toBeCloseTo(0.85);
    expect(defaults.levels).toBe(8);
  });

  it('should have config.enums for depthOfField', () => {
    const enums = PostprocessingPlugin.config!.enums!.depthOfField;
    expect(enums).toBeDefined();
    expect(enums.autoFocus.off).toBe(0);
    expect(enums.autoFocus.on).toBe(1);
  });

  it('should have config.enums for chromaticAberration', () => {
    const enums = PostprocessingPlugin.config!.enums!.chromaticAberration;
    expect(enums).toBeDefined();
    expect(enums.radialModulation.off).toBe(0);
    expect(enums.radialModulation.on).toBe(1);
  });

  it('should have config.enums for noise', () => {
    const enums = PostprocessingPlugin.config!.enums!.noise;
    expect(enums).toBeDefined();
    expect(enums.blendFunction.skip).toBe(0);
    expect(enums.blendFunction.normal).toBe(1);
    expect(enums.blendFunction.darken).toBe(2);
    expect(enums.blendFunction.multiply).toBe(3);
    expect(enums.blendFunction.lighten).toBe(4);
    expect(enums.blendFunction.screen).toBe(5);
    expect(enums.blendFunction.overlay).toBe(6);
  });

  it('should have two systems registered', () => {
    expect(PostprocessingPlugin.systems).toHaveLength(2);
  });
});
