import { afterEach, describe, expect, it } from 'bun:test';
import {
  _resetGltfLoadTrackingForTests,
  _trackGltfLoadForTests,
  getActiveGltfLoadCount,
  getCriticalGltfLoadCount,
  GltfPending,
  gltfAssetsReady,
  State,
} from 'vibegame';

describe('gltf boot assets gate', () => {
  afterEach(() => {
    _resetGltfLoadTrackingForTests();
  });

  it('gltfAssetsReady is true on an empty world', () => {
    const state = new State();
    expect(gltfAssetsReady(state)).toBe(true);
  });

  it('gltfAssetsReady waits for GltfPending entities that have not loaded', () => {
    const state = new State();
    state.registerComponent('gltf-pending', GltfPending);
    const eid = state.createEntity();
    state.addComponent(eid, GltfPending);
    GltfPending.loaded[eid] = 0;
    expect(gltfAssetsReady(state)).toBe(false);

    GltfPending.loaded[eid] = 1;
    expect(gltfAssetsReady(state)).toBe(true);
  });

  it('background loads do not bump the critical counter', async () => {
    expect(getCriticalGltfLoadCount()).toBe(0);
    expect(getActiveGltfLoadCount()).toBe(0);

    let resolve!: () => void;
    const p = _trackGltfLoadForTests(
      new Promise<void>((r) => {
        resolve = r;
      }),
      'background'
    );
    expect(getCriticalGltfLoadCount()).toBe(0);
    expect(getActiveGltfLoadCount()).toBe(1);
    expect(gltfAssetsReady(new State())).toBe(true);

    resolve();
    await p;
    expect(getActiveGltfLoadCount()).toBe(0);
  });

  it('critical loads block gltfAssetsReady until settled', async () => {
    const state = new State();
    let resolve!: () => void;
    const p = _trackGltfLoadForTests(
      new Promise<void>((r) => {
        resolve = r;
      }),
      'critical'
    );
    expect(getCriticalGltfLoadCount()).toBe(1);
    expect(gltfAssetsReady(state)).toBe(false);

    resolve();
    await p;
    expect(getCriticalGltfLoadCount()).toBe(0);
    expect(gltfAssetsReady(state)).toBe(true);
  });
});
