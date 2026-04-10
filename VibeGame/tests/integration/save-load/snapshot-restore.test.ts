import { beforeEach, describe, expect, it } from 'bun:test';
import { Packr } from 'msgpackr';
import { State } from 'vibegame';
import { Serializable } from '../../../src/plugins/save-load/components';
import { SaveLoadPlugin } from '../../../src/plugins/save-load/plugin';
import {
  saveSnapshot,
  loadSnapshot,
} from '../../../src/plugins/save-load/serializer';

const packr = new Packr();

describe('Save-Load Snapshot Restore', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.headless = true;
    state.registerPlugin(SaveLoadPlugin);
  });

  it('should save entities with serializable flag into the snapshot', () => {
    const e1 = state.createEntity();
    state.addComponent(e1, Serializable);
    Serializable.flag[e1] = 1;

    const e2 = state.createEntity();
    state.addComponent(e2, Serializable);
    Serializable.flag[e2] = 1;

    const data = saveSnapshot(state);
    const payload = packr.unpack(data) as {
      elapsed: number;
      entities: Array<{
        eid: number;
        components: Record<string, Record<string, number>>;
      }>;
      serializableEids?: number[];
    };

    expect(payload.serializableEids).toBeDefined();
    expect(payload.serializableEids!).toContain(e1);
    expect(payload.serializableEids!).toContain(e2);
  });

  it('should exclude entities without the serializable flag set', () => {
    const e1 = state.createEntity();
    state.addComponent(e1, Serializable);
    Serializable.flag[e1] = 1;

    const e2 = state.createEntity();
    state.addComponent(e2, Serializable);
    Serializable.flag[e2] = 0;

    const data = saveSnapshot(state);
    const payload = packr.unpack(data) as { serializableEids?: number[] };

    expect(payload.serializableEids).toBeDefined();
    expect(payload.serializableEids!).toContain(e1);
    expect(payload.serializableEids).not.toContain(e2);
  });

  it('should restore elapsed time via loadSnapshot', () => {
    state.time.elapsed = 42.5;

    const e1 = state.createEntity();
    state.addComponent(e1, Serializable);
    Serializable.flag[e1] = 1;

    const data = saveSnapshot(state);

    const state2 = new State();
    state2.headless = true;
    state2.registerPlugin(SaveLoadPlugin);

    loadSnapshot(state2, data);
    expect(state2.time.elapsed).toBeCloseTo(42.5);
  });

  it('should produce a valid msgpack payload with entity components', () => {
    const e1 = state.createEntity();
    state.addComponent(e1, Serializable);
    Serializable.flag[e1] = 1;
    Serializable.serializationId[e1] = 7;

    const data = saveSnapshot(state);
    const payload = packr.unpack(data) as {
      entities: Array<{
        eid: number;
        components: Record<string, Record<string, number>>;
      }>;
    };

    expect(payload.entities.length).toBeGreaterThanOrEqual(1);
    const match = payload.entities.find((e) => e.eid === e1);
    expect(match).toBeDefined();
    expect(match!.components.serializable).toBeDefined();
    expect(match!.components.serializable.flag).toBe(1);
    expect(match!.components.serializable.serializationId).toBe(7);
  });

  it('should handle an empty world snapshot', () => {
    const data = saveSnapshot(state);
    const payload = packr.unpack(data) as {
      elapsed: number;
      entities: unknown[];
      serializableEids?: number[];
    };

    expect(payload.elapsed).toBe(0);
    expect(payload.entities).toEqual([]);
    expect(payload.serializableEids).toEqual([]);
  });
});
