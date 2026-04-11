import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import {
  AudioSource,
  AudioListener,
} from '../../../src/plugins/audio/components';

const EMITTER_FIELDS = [
  'clipPath',
  'volume',
  'loop',
  'pitch',
  'spatial',
  'minDistance',
  'maxDistance',
  'rolloff',
  'playing',
] as const;

describe('AudioEmitter Component', () => {
  let state: State;
  let entity: number;

  beforeEach(() => {
    state = new State();
    entity = state.createEntity();
  });

  it('should have all 9 fields defined', () => {
    for (const field of EMITTER_FIELDS) {
      expect(AudioSource[field]).toBeDefined();
      expect(typeof AudioSource[field][entity]).toBe('number');
    }
  });

  it('should initialize all fields to 0', () => {
    state.addComponent(entity, AudioSource);

    for (const field of EMITTER_FIELDS) {
      expect(AudioSource[field][entity]).toBe(0);
    }
  });

  it('should allow writing and reading clipPath', () => {
    state.addComponent(entity, AudioSource);
    AudioSource.clipPath[entity] = 42;
    expect(AudioSource.clipPath[entity]).toBe(42);
  });

  it('should allow writing and reading volume', () => {
    state.addComponent(entity, AudioSource);
    AudioSource.volume[entity] = 0.75;
    expect(AudioSource.volume[entity]).toBeCloseTo(0.75);
  });

  it('should allow writing and reading loop', () => {
    state.addComponent(entity, AudioSource);
    AudioSource.loop[entity] = 1;
    expect(AudioSource.loop[entity]).toBe(1);
  });

  it('should allow writing and reading pitch', () => {
    state.addComponent(entity, AudioSource);
    AudioSource.pitch[entity] = 1.5;
    expect(AudioSource.pitch[entity]).toBeCloseTo(1.5);
  });

  it('should allow writing and reading spatial mode', () => {
    state.addComponent(entity, AudioSource);
    AudioSource.spatial[entity] = 1;
    expect(AudioSource.spatial[entity]).toBe(1);
  });

  it('should allow writing and reading minDistance and maxDistance', () => {
    state.addComponent(entity, AudioSource);
    AudioSource.minDistance[entity] = 2;
    AudioSource.maxDistance[entity] = 50;
    expect(AudioSource.minDistance[entity]).toBe(2);
    expect(AudioSource.maxDistance[entity]).toBe(50);
  });

  it('should allow writing and reading rolloff', () => {
    state.addComponent(entity, AudioSource);
    AudioSource.rolloff[entity] = 2.5;
    expect(AudioSource.rolloff[entity]).toBeCloseTo(2.5);
  });

  it('should allow writing and reading playing state', () => {
    state.addComponent(entity, AudioSource);
    AudioSource.playing[entity] = 1;
    expect(AudioSource.playing[entity]).toBe(1);
  });

  it('should support multiple entities with independent values', () => {
    state.addComponent(entity, AudioSource);
    const entity2 = state.createEntity();
    state.addComponent(entity2, AudioSource);

    AudioSource.volume[entity] = 1.0;
    AudioSource.volume[entity2] = 0.3;
    AudioSource.clipPath[entity] = 10;
    AudioSource.clipPath[entity2] = 20;

    expect(AudioSource.volume[entity]).toBeCloseTo(1.0);
    expect(AudioSource.volume[entity2]).toBeCloseTo(0.3);
    expect(AudioSource.clipPath[entity]).toBe(10);
    expect(AudioSource.clipPath[entity2]).toBe(20);
  });
});

describe('AudioListener Component', () => {
  let state: State;
  let entity: number;

  beforeEach(() => {
    state = new State();
    entity = state.createEntity();
  });

  it('should be defined as an empty component', () => {
    expect(AudioListener).toBeDefined();
  });

  it('should be addable to an entity', () => {
    state.addComponent(entity, AudioListener);
    expect(state.hasComponent(entity, AudioListener)).toBe(true);
  });
});
