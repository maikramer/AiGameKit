import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import { Serializable } from '../../../src/plugins/save-load/components';

const SERIALIZABLE_FIELDS = ['flag', 'serializationId'] as const;

describe('Serializable Component', () => {
  let state: State;
  let entity: number;

  beforeEach(() => {
    state = new State();
    entity = state.createEntity();
  });

  it('should have all fields defined', () => {
    for (const field of SERIALIZABLE_FIELDS) {
      expect(Serializable[field]).toBeDefined();
      expect(typeof Serializable[field][entity]).toBe('number');
    }
  });

  it('should initialize all fields to 0', () => {
    state.addComponent(entity, Serializable);

    for (const field of SERIALIZABLE_FIELDS) {
      expect(Serializable[field][entity]).toBe(0);
    }
  });

  it('should allow writing and reading flag', () => {
    state.addComponent(entity, Serializable);
    Serializable.flag[entity] = 1;
    expect(Serializable.flag[entity]).toBe(1);
  });

  it('should allow writing and reading serializationId', () => {
    state.addComponent(entity, Serializable);
    Serializable.serializationId[entity] = 42;
    expect(Serializable.serializationId[entity]).toBe(42);
  });

  it('should support write-read roundtrip for all fields', () => {
    state.addComponent(entity, Serializable);
    Serializable.flag[entity] = 1;
    Serializable.serializationId[entity] = 99;

    expect(Serializable.flag[entity]).toBe(1);
    expect(Serializable.serializationId[entity]).toBe(99);
  });

  it('should support multiple entities with independent values', () => {
    state.addComponent(entity, Serializable);
    const entity2 = state.createEntity();
    state.addComponent(entity2, Serializable);

    Serializable.flag[entity] = 1;
    Serializable.flag[entity2] = 0;
    Serializable.serializationId[entity] = 10;
    Serializable.serializationId[entity2] = 20;

    expect(Serializable.flag[entity]).toBe(1);
    expect(Serializable.flag[entity2]).toBe(0);
    expect(Serializable.serializationId[entity]).toBe(10);
    expect(Serializable.serializationId[entity2]).toBe(20);
  });
});
