import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import {
  ParticleSystem,
  ParticleBurst,
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
      expect(ParticleSystem[field]).toBeDefined();
      expect(typeof ParticleSystem[field][entity]).toBe('number');
    }
  });

  it('should initialize all fields to 0', () => {
    state.addComponent(entity, ParticleSystem);

    for (const field of EMITTER_FIELDS) {
      expect(ParticleSystem[field][entity]).toBe(0);
    }
  });

  it('should allow writing and reading preset', () => {
    state.addComponent(entity, ParticleSystem);
    ParticleSystem.preset[entity] = 4;
    expect(ParticleSystem.preset[entity]).toBe(4);
  });

  it('should allow writing and reading rate', () => {
    state.addComponent(entity, ParticleSystem);
    ParticleSystem.rate[entity] = 20;
    expect(ParticleSystem.rate[entity]).toBeCloseTo(20);
  });

  it('should allow writing and reading lifetime', () => {
    state.addComponent(entity, ParticleSystem);
    ParticleSystem.lifetime[entity] = 2.5;
    expect(ParticleSystem.lifetime[entity]).toBeCloseTo(2.5);
  });

  it('should allow writing and reading size', () => {
    state.addComponent(entity, ParticleSystem);
    ParticleSystem.size[entity] = 0.3;
    expect(ParticleSystem.size[entity]).toBeCloseTo(0.3);
  });

  it('should allow writing and reading looping and playing', () => {
    state.addComponent(entity, ParticleSystem);
    ParticleSystem.looping[entity] = 1;
    ParticleSystem.playing[entity] = 1;
    expect(ParticleSystem.looping[entity]).toBe(1);
    expect(ParticleSystem.playing[entity]).toBe(1);
  });

  it('should allow writing and reading spawned', () => {
    state.addComponent(entity, ParticleSystem);
    ParticleSystem.spawned[entity] = 1;
    expect(ParticleSystem.spawned[entity]).toBe(1);
  });

  it('should support multiple entities with independent values', () => {
    state.addComponent(entity, ParticleSystem);
    const entity2 = state.createEntity();
    state.addComponent(entity2, ParticleSystem);

    ParticleSystem.preset[entity] = 0;
    ParticleSystem.preset[entity2] = 5;
    ParticleSystem.rate[entity] = 10;
    ParticleSystem.rate[entity2] = 30;

    expect(ParticleSystem.preset[entity]).toBe(0);
    expect(ParticleSystem.preset[entity2]).toBe(5);
    expect(ParticleSystem.rate[entity]).toBeCloseTo(10);
    expect(ParticleSystem.rate[entity2]).toBeCloseTo(30);
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
      expect(ParticleBurst[field]).toBeDefined();
      expect(typeof ParticleBurst[field][entity]).toBe('number');
    }
  });

  it('should initialize all fields to 0', () => {
    state.addComponent(entity, ParticleBurst);

    for (const field of BURST_FIELDS) {
      expect(ParticleBurst[field][entity]).toBe(0);
    }
  });

  it('should allow writing and reading preset', () => {
    state.addComponent(entity, ParticleBurst);
    ParticleBurst.preset[entity] = 2;
    expect(ParticleBurst.preset[entity]).toBe(2);
  });

  it('should allow writing and reading count', () => {
    state.addComponent(entity, ParticleBurst);
    ParticleBurst.count[entity] = 100;
    expect(ParticleBurst.count[entity]).toBeCloseTo(100);
  });

  it('should allow writing and reading triggered', () => {
    state.addComponent(entity, ParticleBurst);
    ParticleBurst.triggered[entity] = 1;
    expect(ParticleBurst.triggered[entity]).toBe(1);
  });

  it('should support multiple entities with independent values', () => {
    state.addComponent(entity, ParticleBurst);
    const entity2 = state.createEntity();
    state.addComponent(entity2, ParticleBurst);

    ParticleBurst.preset[entity] = 0;
    ParticleBurst.preset[entity2] = 99;
    ParticleBurst.count[entity] = 50;
    ParticleBurst.count[entity2] = 200;

    expect(ParticleBurst.preset[entity]).toBe(0);
    expect(ParticleBurst.preset[entity2]).toBe(99);
    expect(ParticleBurst.count[entity]).toBeCloseTo(50);
    expect(ParticleBurst.count[entity2]).toBeCloseTo(200);
  });
});
