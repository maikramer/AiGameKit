import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import {
  Vignette,
  DepthOfField,
  ChromaticAberration,
  Noise,
  Bloom,
} from '../../../src/plugins/postprocessing/components';

const VIGNETTE_FIELDS = ['darkness', 'offset'] as const;
const DOF_FIELDS = [
  'focusDistance',
  'focalLength',
  'bokehScale',
  'resolutionScale',
  'autoFocus',
] as const;
const CA_FIELDS = [
  'offsetX',
  'offsetY',
  'radialModulation',
  'modulationOffset',
] as const;
const NOISE_FIELDS = ['opacity', 'blendFunction'] as const;
const BLOOM_FIELDS = [
  'intensity',
  'luminanceThreshold',
  'luminanceSmoothing',
  'mipmapBlur',
  'radius',
  'levels',
] as const;

describe('Vignette Component', () => {
  let state: State;
  let entity: number;

  beforeEach(() => {
    state = new State();
    entity = state.createEntity();
  });

  it('should have all fields defined', () => {
    for (const field of VIGNETTE_FIELDS) {
      expect(Vignette[field]).toBeDefined();
      expect(typeof Vignette[field][entity]).toBe('number');
    }
  });

  it('should initialize all fields to 0', () => {
    state.addComponent(entity, Vignette);
    for (const field of VIGNETTE_FIELDS) {
      expect(Vignette[field][entity]).toBe(0);
    }
  });

  it('should allow writing and reading darkness and offset', () => {
    state.addComponent(entity, Vignette);
    Vignette.darkness[entity] = 0.5;
    Vignette.offset[entity] = 0.1;
    expect(Vignette.darkness[entity]).toBeCloseTo(0.5);
    expect(Vignette.offset[entity]).toBeCloseTo(0.1);
  });

  it('should support multiple entities with independent values', () => {
    state.addComponent(entity, Vignette);
    const entity2 = state.createEntity();
    state.addComponent(entity2, Vignette);

    Vignette.darkness[entity] = 0.8;
    Vignette.darkness[entity2] = 0.3;
    Vignette.offset[entity] = 0.2;
    Vignette.offset[entity2] = 0.5;

    expect(Vignette.darkness[entity]).toBeCloseTo(0.8);
    expect(Vignette.darkness[entity2]).toBeCloseTo(0.3);
    expect(Vignette.offset[entity]).toBeCloseTo(0.2);
    expect(Vignette.offset[entity2]).toBeCloseTo(0.5);
  });
});

describe('DepthOfField Component', () => {
  let state: State;
  let entity: number;

  beforeEach(() => {
    state = new State();
    entity = state.createEntity();
  });

  it('should have all 5 fields defined', () => {
    for (const field of DOF_FIELDS) {
      expect(DepthOfField[field]).toBeDefined();
      expect(typeof DepthOfField[field][entity]).toBe('number');
    }
  });

  it('should initialize all fields to 0', () => {
    state.addComponent(entity, DepthOfField);
    for (const field of DOF_FIELDS) {
      expect(DepthOfField[field][entity]).toBe(0);
    }
  });

  it('should allow writing and reading focus distance and focal length', () => {
    state.addComponent(entity, DepthOfField);
    DepthOfField.focusDistance[entity] = 10;
    DepthOfField.focalLength[entity] = 0.05;
    expect(DepthOfField.focusDistance[entity]).toBeCloseTo(10);
    expect(DepthOfField.focalLength[entity]).toBeCloseTo(0.05);
  });

  it('should allow writing and reading bokeh scale, resolution scale, and autoFocus', () => {
    state.addComponent(entity, DepthOfField);
    DepthOfField.bokehScale[entity] = 2;
    DepthOfField.resolutionScale[entity] = 0.5;
    DepthOfField.autoFocus[entity] = 1;
    expect(DepthOfField.bokehScale[entity]).toBeCloseTo(2);
    expect(DepthOfField.resolutionScale[entity]).toBeCloseTo(0.5);
    expect(DepthOfField.autoFocus[entity]).toBe(1);
  });

  it('should support multiple entities with independent values', () => {
    state.addComponent(entity, DepthOfField);
    const entity2 = state.createEntity();
    state.addComponent(entity2, DepthOfField);

    DepthOfField.focusDistance[entity] = 5;
    DepthOfField.focusDistance[entity2] = 20;
    DepthOfField.autoFocus[entity] = 0;
    DepthOfField.autoFocus[entity2] = 1;

    expect(DepthOfField.focusDistance[entity]).toBeCloseTo(5);
    expect(DepthOfField.focusDistance[entity2]).toBeCloseTo(20);
    expect(DepthOfField.autoFocus[entity]).toBe(0);
    expect(DepthOfField.autoFocus[entity2]).toBe(1);
  });
});

describe('ChromaticAberration Component', () => {
  let state: State;
  let entity: number;

  beforeEach(() => {
    state = new State();
    entity = state.createEntity();
  });

  it('should have all 4 fields defined', () => {
    for (const field of CA_FIELDS) {
      expect(ChromaticAberration[field]).toBeDefined();
      expect(typeof ChromaticAberration[field][entity]).toBe('number');
    }
  });

  it('should initialize all fields to 0', () => {
    state.addComponent(entity, ChromaticAberration);
    for (const field of CA_FIELDS) {
      expect(ChromaticAberration[field][entity]).toBe(0);
    }
  });

  it('should allow writing and reading offset values', () => {
    state.addComponent(entity, ChromaticAberration);
    ChromaticAberration.offsetX[entity] = 0.002;
    ChromaticAberration.offsetY[entity] = 0.001;
    expect(ChromaticAberration.offsetX[entity]).toBeCloseTo(0.002);
    expect(ChromaticAberration.offsetY[entity]).toBeCloseTo(0.001);
  });

  it('should allow writing and reading radial modulation and modulation offset', () => {
    state.addComponent(entity, ChromaticAberration);
    ChromaticAberration.radialModulation[entity] = 1;
    ChromaticAberration.modulationOffset[entity] = 0.15;
    expect(ChromaticAberration.radialModulation[entity]).toBe(1);
    expect(ChromaticAberration.modulationOffset[entity]).toBeCloseTo(0.15);
  });

  it('should support multiple entities with independent values', () => {
    state.addComponent(entity, ChromaticAberration);
    const entity2 = state.createEntity();
    state.addComponent(entity2, ChromaticAberration);

    ChromaticAberration.offsetX[entity] = 0.005;
    ChromaticAberration.offsetX[entity2] = 0.001;
    ChromaticAberration.radialModulation[entity] = 0;
    ChromaticAberration.radialModulation[entity2] = 1;

    expect(ChromaticAberration.offsetX[entity]).toBeCloseTo(0.005);
    expect(ChromaticAberration.offsetX[entity2]).toBeCloseTo(0.001);
    expect(ChromaticAberration.radialModulation[entity]).toBe(0);
    expect(ChromaticAberration.radialModulation[entity2]).toBe(1);
  });
});

describe('Noise Component', () => {
  let state: State;
  let entity: number;

  beforeEach(() => {
    state = new State();
    entity = state.createEntity();
  });

  it('should have all 2 fields defined', () => {
    for (const field of NOISE_FIELDS) {
      expect(Noise[field]).toBeDefined();
      expect(typeof Noise[field][entity]).toBe('number');
    }
  });

  it('should initialize all fields to 0', () => {
    state.addComponent(entity, Noise);
    for (const field of NOISE_FIELDS) {
      expect(Noise[field][entity]).toBe(0);
    }
  });

  it('should allow writing and reading opacity and blend function', () => {
    state.addComponent(entity, Noise);
    Noise.opacity[entity] = 0.2;
    Noise.blendFunction[entity] = 3;
    expect(Noise.opacity[entity]).toBeCloseTo(0.2);
    expect(Noise.blendFunction[entity]).toBe(3);
  });

  it('should support multiple entities with independent values', () => {
    state.addComponent(entity, Noise);
    const entity2 = state.createEntity();
    state.addComponent(entity2, Noise);

    Noise.opacity[entity] = 0.1;
    Noise.opacity[entity2] = 0.5;
    Noise.blendFunction[entity] = 0;
    Noise.blendFunction[entity2] = 2;

    expect(Noise.opacity[entity]).toBeCloseTo(0.1);
    expect(Noise.opacity[entity2]).toBeCloseTo(0.5);
    expect(Noise.blendFunction[entity]).toBe(0);
    expect(Noise.blendFunction[entity2]).toBe(2);
  });
});

describe('Bloom Component', () => {
  let state: State;
  let entity: number;

  beforeEach(() => {
    state = new State();
    entity = state.createEntity();
  });

  it('should have all 6 fields defined', () => {
    for (const field of BLOOM_FIELDS) {
      expect(Bloom[field]).toBeDefined();
      expect(typeof Bloom[field][entity]).toBe('number');
    }
  });

  it('should initialize all fields to 0', () => {
    state.addComponent(entity, Bloom);
    for (const field of BLOOM_FIELDS) {
      expect(Bloom[field][entity]).toBe(0);
    }
  });

  it('should allow writing and reading intensity and luminance fields', () => {
    state.addComponent(entity, Bloom);
    Bloom.intensity[entity] = 1.5;
    Bloom.luminanceThreshold[entity] = 0.9;
    Bloom.luminanceSmoothing[entity] = 0.025;
    expect(Bloom.intensity[entity]).toBeCloseTo(1.5);
    expect(Bloom.luminanceThreshold[entity]).toBeCloseTo(0.9);
    expect(Bloom.luminanceSmoothing[entity]).toBeCloseTo(0.025);
  });

  it('should allow writing and reading mipmapBlur, radius, and levels', () => {
    state.addComponent(entity, Bloom);
    Bloom.mipmapBlur[entity] = 1;
    Bloom.radius[entity] = 0.85;
    Bloom.levels[entity] = 8;
    expect(Bloom.mipmapBlur[entity]).toBe(1);
    expect(Bloom.radius[entity]).toBeCloseTo(0.85);
    expect(Bloom.levels[entity]).toBe(8);
  });

  it('should support multiple entities with independent values', () => {
    state.addComponent(entity, Bloom);
    const entity2 = state.createEntity();
    state.addComponent(entity2, Bloom);

    Bloom.intensity[entity] = 0.5;
    Bloom.intensity[entity2] = 2.0;
    Bloom.luminanceSmoothing[entity] = 0.1;
    Bloom.luminanceSmoothing[entity2] = 0.4;

    expect(Bloom.intensity[entity]).toBeCloseTo(0.5);
    expect(Bloom.intensity[entity2]).toBeCloseTo(2.0);
    expect(Bloom.luminanceSmoothing[entity]).toBeCloseTo(0.1);
    expect(Bloom.luminanceSmoothing[entity2]).toBeCloseTo(0.4);
  });
});
