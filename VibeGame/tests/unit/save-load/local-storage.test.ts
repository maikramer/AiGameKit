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
});
