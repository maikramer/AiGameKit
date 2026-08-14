import { afterEach, beforeEach, describe, expect, it } from 'bun:test';
import { State } from '../../../src/core';
import {
  loadFromLocalStorage,
  saveToLocalStorage,
} from '../../../src/plugins/save-load/serializer';
import { Transform } from '../../../src/plugins/transforms';

// localStorage doesn't exist in bun — a tiny in-memory mock.
function installLocalStorageMock(): void {
  const store = new Map<string, string>();
  (globalThis as Record<string, unknown>).localStorage = {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => {
      store.set(k, v);
    },
    removeItem: (k: string) => {
      store.delete(k);
    },
    clear: () => store.clear(),
    key: (i: number) => [...store.keys()][i] ?? null,
    get length() {
      return store.size;
    },
  };
}

describe('localStorage persistence', () => {
  beforeEach(() => installLocalStorageMock());
  afterEach(() => {
    delete (globalThis as Record<string, unknown>).localStorage;
  });

  it('round-trips a state through the compressed save format', async () => {
    const state = new State();
    state.registerComponent('transform', Transform);
    const eid = state.createEntity();
    state.setEntityName('player', eid);
    state.addComponent(eid, Transform);
    Transform.posX[eid] = 12.5;
    Transform.posY[eid] = -3;
    Transform.posZ[eid] = 42;

    const key = 'test-save';
    await saveToLocalStorage(state, key);

    const raw = (globalThis as Record<string, unknown>).localStorage as {
      getItem(k: string): string | null;
    };
    const stored = raw.getItem(key);
    expect(stored).not.toBeNull();

    const fresh = new State();
    fresh.registerComponent('transform', Transform);
    expect(await loadFromLocalStorage(fresh, key)).toBe(true);

    const loaded = fresh.getEntityByName('player');
    expect(loaded).not.toBeNull();
    if (loaded === null) return;
    expect(Transform.posX[loaded]).toBeCloseTo(12.5, 5);
    expect(Transform.posZ[loaded]).toBeCloseTo(42, 5);
  });

  it('reads legacy JSON-array saves (pre-compression format)', async () => {
    // Build a snapshot the old way and store it as a plain number array.
    const state = new State();
    state.registerComponent('transform', Transform);
    const eid = state.createEntity();
    state.setEntityName('player', eid);
    state.addComponent(eid, Transform);
    Transform.posX[eid] = 7;

    const { saveSnapshot } =
      await import('../../../src/plugins/save-load/serializer');
    const buf = saveSnapshot(state);
    const legacy = JSON.stringify(Array.from(buf));

    const store = globalThis.localStorage as unknown as {
      setItem(k: string, v: string): void;
    };
    store.setItem('legacy-save', legacy);

    const fresh = new State();
    fresh.registerComponent('transform', Transform);
    expect(await loadFromLocalStorage(fresh, 'legacy-save')).toBe(true);
    const loaded = fresh.getEntityByName('player');
    expect(loaded).not.toBeNull();
    if (loaded !== null) expect(Transform.posX[loaded]).toBeCloseTo(7, 5);
  });

  it('returns false when no save exists', async () => {
    const state = new State();
    expect(await loadFromLocalStorage(state, 'missing')).toBe(false);
  });

  it('returns false on corrupt saves instead of rejecting (garbage base64)', async () => {
    const store = globalThis.localStorage as unknown as {
      setItem(k: string, v: string): void;
    };
    store.setItem('corrupt-save', 'vg1:!!!not-base64!!!');
    const state = new State();
    state.registerComponent('transform', Transform);
    const eid = state.createEntity();
    state.addComponent(eid, Transform);
    Transform.posX[eid] = 99;
    expect(await loadFromLocalStorage(state, 'corrupt-save')).toBe(false);
    // Live world untouched by the corrupt save.
    expect(Transform.posX[eid]).toBe(99);
  });

  it('returns false when the key holds foreign (non-snapshot) data', async () => {
    const store = globalThis.localStorage as unknown as {
      setItem(k: string, v: string): void;
    };
    store.setItem('foreign-save', JSON.stringify({ hello: 'world' }));
    store.setItem('stringy-save', JSON.stringify('just a string'));
    store.setItem('floaty-save', JSON.stringify([1.5, -2, 300]));
    const state = new State();
    expect(await loadFromLocalStorage(state, 'foreign-save')).toBe(false);
    expect(await loadFromLocalStorage(state, 'stringy-save')).toBe(false);
    expect(await loadFromLocalStorage(state, 'floaty-save')).toBe(false);
  });

  it('loadSnapshot rejects msgpack garbage with a clear error and never clears the world first', () => {
    const { loadSnapshot } =
      require('../../../src/plugins/save-load/serializer') as typeof import('../../../src/plugins/save-load/serializer');
    const state = new State();
    state.registerComponent('transform', Transform);
    const eid = state.createEntity();
    state.addComponent(eid, Transform);
    Transform.posX[eid] = 7;
    expect(() =>
      loadSnapshot(state, new Uint8Array([0xc1, 0xff, 0x00]), {
        clearExisting: true,
      })
    ).toThrow(/valid world snapshot/);
    expect(state.exists(eid)).toBe(true);
    expect(Transform.posX[eid]).toBe(7);
  });

  it('save survives a quota error after evicting the previous save', async () => {
    const store = new Map<string, string>();
    let quotaHits = 0;
    (globalThis as Record<string, unknown>).localStorage = {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => {
        if (quotaHits++ === 0) {
          throw new DOMException('quota exceeded', 'QuotaExceededError');
        }
        store.set(k, v);
      },
      removeItem: (k: string) => {
        store.delete(k);
      },
    };
    const state = new State();
    state.registerComponent('transform', Transform);
    const eid = state.createEntity();
    state.addComponent(eid, Transform);
    Transform.posX[eid] = 3;
    expect(await saveToLocalStorage(state, 'quota-save')).toBe(true);
    expect(store.has('quota-save')).toBe(true);
  });

  it('save returns false (never throws) when the quota is truly exhausted', async () => {
    (globalThis as Record<string, unknown>).localStorage = {
      getItem: () => null,
      setItem: () => {
        throw new DOMException('quota exceeded', 'QuotaExceededError');
      },
      removeItem: () => {},
    };
    const state = new State();
    state.registerComponent('transform', Transform);
    const eid = state.createEntity();
    state.addComponent(eid, Transform);
    await expect(saveToLocalStorage(state, 'doomed-save')).resolves.toBe(false);
  });
});
