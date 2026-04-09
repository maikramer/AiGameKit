import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import {
  AudioEmitter,
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
      expect(AudioEmitter[field]).toBeDefined();
      expect(typeof AudioEmitter[field][entity]).toBe('number');
    }
  });

  it('should initialize all fields to 0', () => {
    state.addComponent(entity, AudioEmitter);

    for (const field of EMITTER_FIELDS) {
      expect(AudioEmitter[field][entity]).toBe(0);
    }
  });

  it('should allow writing and reading clipPath', () => {
    state.addComponent(entity, AudioEmitter);
    AudioEmitter.clipPath[entity] = 42;
    expect(AudioEmitter.clipPath[entity]).toBe(42);
  });

  it('should allow writing and reading volume', () => {
    state.addComponent(entity, AudioEmitter);
    AudioEmitter.volume[entity] = 0.75;
    expect(AudioEmitter.volume[entity]).toBeCloseTo(0.75);
  });

  it('should allow writing and reading loop', () => {
    state.addComponent(entity, AudioEmitter);
    AudioEmitter.loop[entity] = 1;
    expect(AudioEmitter.loop[entity]).toBe(1);
  });

  it('should allow writing and reading pitch', () => {
    state.addComponent(entity, AudioEmitter);
    AudioEmitter.pitch[entity] = 1.5;
    expect(AudioEmitter.pitch[entity]).toBeCloseTo(1.5);
  });

  it('should allow writing and reading spatial mode', () => {
    state.addComponent(entity, AudioEmitter);
    AudioEmitter.spatial[entity] = 1;
    expect(AudioEmitter.spatial[entity]).toBe(1);
  });

  it('should allow writing and reading minDistance and maxDistance', () => {
    state.addComponent(entity, AudioEmitter);
    AudioEmitter.minDistance[entity] = 2;
    AudioEmitter.maxDistance[entity] = 50;
    expect(AudioEmitter.minDistance[entity]).toBe(2);
    expect(AudioEmitter.maxDistance[entity]).toBe(50);
  });

  it('should allow writing and reading rolloff', () => {
    state.addComponent(entity, AudioEmitter);
    AudioEmitter.rolloff[entity] = 2.5;
    expect(AudioEmitter.rolloff[entity]).toBeCloseTo(2.5);
  });

  it('should allow writing and reading playing state', () => {
    state.addComponent(entity, AudioEmitter);
    AudioEmitter.playing[entity] = 1;
    expect(AudioEmitter.playing[entity]).toBe(1);
  });

  it('should support multiple entities with independent values', () => {
    state.addComponent(entity, AudioEmitter);
    const entity2 = state.createEntity();
    state.addComponent(entity2, AudioEmitter);

    AudioEmitter.volume[entity] = 1.0;
    AudioEmitter.volume[entity2] = 0.3;
    AudioEmitter.clipPath[entity] = 10;
    AudioEmitter.clipPath[entity2] = 20;

    expect(AudioEmitter.volume[entity]).toBeCloseTo(1.0);
    expect(AudioEmitter.volume[entity2]).toBeCloseTo(0.3);
    expect(AudioEmitter.clipPath[entity]).toBe(10);
    expect(AudioEmitter.clipPath[entity2]).toBe(20);
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
