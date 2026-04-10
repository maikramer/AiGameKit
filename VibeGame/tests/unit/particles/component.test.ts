import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import {
  ParticlesEmitter,
  ParticlesBurst,
} from '../../../src/plugins/particles/components';

const EMITTER_FIELDS = [
  'preset',
  'rate',
  'lifetime',
  'size',
  'looping',
  'playing',
  'spawned',
] as const;

const BURST_FIELDS = ['preset', 'count', 'triggered'] as const;

describe('ParticlesEmitter Component', () => {
  let state: State;
  let entity: number;

  beforeEach(() => {
    state = new State();
    entity = state.createEntity();
  });

  it('should have all 7 fields defined', () => {
    for (const field of EMITTER_FIELDS) {
      expect(ParticlesEmitter[field]).toBeDefined();
      expect(typeof ParticlesEmitter[field][entity]).toBe('number');
    }
  });

  it('should initialize all fields to 0', () => {
    state.addComponent(entity, ParticlesEmitter);

    for (const field of EMITTER_FIELDS) {
      expect(ParticlesEmitter[field][entity]).toBe(0);
    }
  });

  it('should allow writing and reading preset', () => {
    state.addComponent(entity, ParticlesEmitter);
    ParticlesEmitter.preset[entity] = 4;
    expect(ParticlesEmitter.preset[entity]).toBe(4);
  });

  it('should allow writing and reading rate', () => {
    state.addComponent(entity, ParticlesEmitter);
    ParticlesEmitter.rate[entity] = 20;
    expect(ParticlesEmitter.rate[entity]).toBeCloseTo(20);
  });

  it('should allow writing and reading lifetime', () => {
    state.addComponent(entity, ParticlesEmitter);
    ParticlesEmitter.lifetime[entity] = 2.5;
    expect(ParticlesEmitter.lifetime[entity]).toBeCloseTo(2.5);
  });

  it('should allow writing and reading size', () => {
    state.addComponent(entity, ParticlesEmitter);
    ParticlesEmitter.size[entity] = 0.3;
    expect(ParticlesEmitter.size[entity]).toBeCloseTo(0.3);
  });

  it('should allow writing and reading looping and playing', () => {
    state.addComponent(entity, ParticlesEmitter);
    ParticlesEmitter.looping[entity] = 1;
    ParticlesEmitter.playing[entity] = 1;
    expect(ParticlesEmitter.looping[entity]).toBe(1);
    expect(ParticlesEmitter.playing[entity]).toBe(1);
  });

  it('should allow writing and reading spawned', () => {
    state.addComponent(entity, ParticlesEmitter);
    ParticlesEmitter.spawned[entity] = 1;
    expect(ParticlesEmitter.spawned[entity]).toBe(1);
  });

  it('should support multiple entities with independent values', () => {
    state.addComponent(entity, ParticlesEmitter);
    const entity2 = state.createEntity();
    state.addComponent(entity2, ParticlesEmitter);

    ParticlesEmitter.preset[entity] = 0;
    ParticlesEmitter.preset[entity2] = 5;
    ParticlesEmitter.rate[entity] = 10;
    ParticlesEmitter.rate[entity2] = 30;

    expect(ParticlesEmitter.preset[entity]).toBe(0);
    expect(ParticlesEmitter.preset[entity2]).toBe(5);
    expect(ParticlesEmitter.rate[entity]).toBeCloseTo(10);
    expect(ParticlesEmitter.rate[entity2]).toBeCloseTo(30);
  });
});

describe('ParticlesBurst Component', () => {
  let state: State;
  let entity: number;

  beforeEach(() => {
    state = new State();
    entity = state.createEntity();
  });

  it('should have all 3 fields defined', () => {
    for (const field of BURST_FIELDS) {
      expect(ParticlesBurst[field]).toBeDefined();
      expect(typeof ParticlesBurst[field][entity]).toBe('number');
    }
  });

  it('should initialize all fields to 0', () => {
    state.addComponent(entity, ParticlesBurst);

    for (const field of BURST_FIELDS) {
      expect(ParticlesBurst[field][entity]).toBe(0);
    }
  });

  it('should allow writing and reading preset', () => {
    state.addComponent(entity, ParticlesBurst);
    ParticlesBurst.preset[entity] = 2;
    expect(ParticlesBurst.preset[entity]).toBe(2);
  });

  it('should allow writing and reading count', () => {
    state.addComponent(entity, ParticlesBurst);
    ParticlesBurst.count[entity] = 100;
    expect(ParticlesBurst.count[entity]).toBeCloseTo(100);
  });

  it('should allow writing and reading triggered', () => {
    state.addComponent(entity, ParticlesBurst);
    ParticlesBurst.triggered[entity] = 1;
    expect(ParticlesBurst.triggered[entity]).toBe(1);
  });

  it('should support multiple entities with independent values', () => {
    state.addComponent(entity, ParticlesBurst);
    const entity2 = state.createEntity();
    state.addComponent(entity2, ParticlesBurst);

    ParticlesBurst.preset[entity] = 0;
    ParticlesBurst.preset[entity2] = 99;
    ParticlesBurst.count[entity] = 50;
    ParticlesBurst.count[entity2] = 200;

    expect(ParticlesBurst.preset[entity]).toBe(0);
    expect(ParticlesBurst.preset[entity2]).toBe(99);
    expect(ParticlesBurst.count[entity]).toBeCloseTo(50);
    expect(ParticlesBurst.count[entity2]).toBeCloseTo(200);
  });
});
