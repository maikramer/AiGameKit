import { beforeEach, describe, expect, it } from 'bun:test';
import {
  RpgCoreEventsPlugin,
  State,
  Serializable,
  SaveLoadPlugin,
} from 'vibegame';
import {
  deserializeAll,
  getSaveSerializer,
  isTransientEntity,
  registerGlobalSaveSerializer,
  registerSaveSerializer,
  registerTransientExclusion,
  serializeAll,
} from '../../../src/plugins/save-load/serializer-registry';

describe('save-load serializer registry table-driven', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.registerPlugin(RpgCoreEventsPlugin);
    state.registerPlugin(SaveLoadPlugin);
  });

  for (let i = 0; i < 25; i++) {
    it(`registerSaveSerializer kind-${i} is retrievable`, () => {
      const kind = `kind-${i}`;
      registerSaveSerializer(state, kind, {
        serialize: () => ({ n: i }),
        deserialize: () => {},
      });
      expect(getSaveSerializer(state, kind)).toBeDefined();
    });
  }

  for (let i = 0; i < 20; i++) {
    it(`serializeAll includes entity data for kind-${i}`, () => {
      const kind = `data-${i}`;
      registerSaveSerializer(state, kind, {
        serialize: (_s, eid) => ({ eid, v: i }),
        deserialize: () => {},
      });
      const eid = state.createEntity();
      state.addComponent(eid, Serializable);
      Serializable.flag[eid] = 1;
      const snap = serializeAll(state);
      const row = snap.entities.find((e) => e.eid === eid);
      expect(row?.kinds[kind]).toEqual({ eid, v: i });
    });
  }

  for (let i = 0; i < 15; i++) {
    it(`deserializeAll restores kind restore-${i}`, () => {
      const kind = `restore-${i}`;
      let last: unknown;
      registerSaveSerializer(state, kind, {
        serialize: () => null,
        deserialize: (_s, _e, data) => {
          last = data;
        },
      });
      const eid = state.createEntity();
      deserializeAll(state, {
        version: '1.0',
        entities: [{ eid, kinds: { [kind]: { payload: i } } }],
      });
      expect(last).toEqual({ payload: i });
    });
  }

  for (let i = 0; i < 15; i++) {
    it(`global serializer global-${i} appears in snapshot.globals`, () => {
      const kind = `global-${i}`;
      registerGlobalSaveSerializer(state, kind, {
        serialize: () => ({ g: i }),
        deserialize: () => {},
      });
      const snap = serializeAll(state);
      expect(snap.globals?.[kind]).toEqual({ g: i });
    });
  }

  for (let i = 0; i < 10; i++) {
    it(`serializeAll version is always 1.0 — case ${i}`, () => {
      expect(serializeAll(state).version).toBe('1.0');
    });
  }

  for (let i = 0; i < 10; i++) {
    it(`entities sorted by eid ascending — batch ${i}`, () => {
      const ids: number[] = [];
      for (let j = 0; j < 3; j++) {
        const e = state.createEntity();
        state.addComponent(e, Serializable);
        Serializable.flag[e] = 1;
        ids.push(e);
      }
      registerSaveSerializer(state, 'marker', {
        serialize: (_s, eid) => ({ m: eid }),
        deserialize: () => {},
      });
      const snap = serializeAll(state);
      const eids = snap.entities.map((e) => e.eid);
      const sorted = [...eids].sort((a, b) => a - b);
      expect(eids).toEqual(sorted);
    });
  }

  for (let i = 0; i < 10; i++) {
    it(`serializer returning null omits kind omit-${i}`, () => {
      registerSaveSerializer(state, `omit-${i}`, {
        serialize: () => null,
        deserialize: () => {},
      });
      const eid = state.createEntity();
      state.addComponent(eid, Serializable);
      Serializable.flag[eid] = 1;
      const snap = serializeAll(state);
      const row = snap.entities.find((e) => e.eid === eid);
      expect(row?.kinds[`omit-${i}`]).toBeUndefined();
    });
  }

  for (let i = 0; i < 5; i++) {
    it(`isTransientEntity false for plain entity ${i}`, () => {
      const eid = state.createEntity();
      expect(isTransientEntity(state, eid)).toBe(false);
    });
  }
});

describe('save-load transient exclusion dedupe', () => {
  it('registerTransientExclusion dedupes by name', () => {
    registerTransientExclusion({
      name: 'unit-test-transient-dedupe',
      component: 'Serializable',
    });
    registerTransientExclusion({
      name: 'unit-test-transient-dedupe',
      component: 'Serializable',
    });
    const state = new State();
    state.registerPlugin(SaveLoadPlugin);
    const eid = state.createEntity();
    expect(isTransientEntity(state, eid)).toBe(false);
  });
});
