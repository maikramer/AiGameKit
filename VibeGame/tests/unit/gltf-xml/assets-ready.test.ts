import { afterEach, describe, expect, it } from 'bun:test';
import {
  _resetGltfLoadTrackingForTests,
  _trackGltfLoadForTests,
  describeGltfAssetsPending,
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

  it('gltfAssetsReady waits for GltfPending entities that have not been kicked', () => {
    const state = new State();
    state.registerComponent('gltf-pending', GltfPending);
    const eid = state.createEntity();
    state.addComponent(eid, GltfPending);
    GltfPending.loaded[eid] = 0;
    expect(gltfAssetsReady(state)).toBe(false);

    GltfPending.loaded[eid] = 1;
    expect(gltfAssetsReady(state)).toBe(true);
  });

  it('duplicate critical URL tracks count once', async () => {
    let resolve!: () => void;
    const shared = new Promise<void>((r) => {
      resolve = r;
    });
    const a = _trackGltfLoadForTests(shared, 'critical', '/a.glb');
    const b = _trackGltfLoadForTests(shared, 'critical', '/a.glb');
    expect(getCriticalGltfLoadCount()).toBe(1);
    resolve();
    await Promise.all([a, b]);
    expect(getCriticalGltfLoadCount()).toBe(0);
  });

  it('critical request promotes a URL already tracked as background', async () => {
    let resolve!: () => void;
    const shared = new Promise<void>((r) => {
      resolve = r;
    });
    void _trackGltfLoadForTests(shared, 'background', '/prop_lod1.glb');
    expect(getCriticalGltfLoadCount()).toBe(0);
    expect(getActiveGltfLoadCount()).toBe(1);

    void _trackGltfLoadForTests(shared, 'critical', '/prop_lod1.glb');
    expect(getCriticalGltfLoadCount()).toBe(1);
    expect(getActiveGltfLoadCount()).toBe(1);

    resolve();
    await shared;
    expect(getCriticalGltfLoadCount()).toBe(0);
    expect(getActiveGltfLoadCount()).toBe(0);
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

  it('a re-request of an already-parsed master does not re-arm the gate', async () => {
    const state = new State();
    await _trackGltfLoadForTests(
      Promise.resolve('gltf'),
      'critical',
      '/hero.glb'
    );
    expect(getCriticalGltfLoadCount()).toBe(0);

    // Per-frame caller re-requesting the cached master (creature animators).
    for (let i = 0; i < 3; i++) {
      void _trackGltfLoadForTests(
        Promise.resolve('gltf'),
        'critical',
        '/hero.glb'
      );
      expect(getCriticalGltfLoadCount()).toBe(0);
      expect(gltfAssetsReady(state)).toBe(true);
    }
  });

  it('a failed critical load can still hold the gate on retry', async () => {
    const first = _trackGltfLoadForTests(
      Promise.reject(new Error('404')),
      'critical',
      '/broken.glb'
    );
    await expect(first).rejects.toThrow('404');
    expect(getCriticalGltfLoadCount()).toBe(0);

    let resolve!: () => void;
    const retry = _trackGltfLoadForTests(
      new Promise<void>((r) => {
        resolve = r;
      }),
      'critical',
      '/broken.glb'
    );
    expect(getCriticalGltfLoadCount()).toBe(1);
    resolve();
    await retry;
    expect(getCriticalGltfLoadCount()).toBe(0);
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
    expect(describeGltfAssetsPending(state).critical).toBe(1);
    expect(describeGltfAssetsPending(state).remaining).toBe(1);
    expect(describeGltfAssetsPending(state).total).toBe(1);
    expect(describeGltfAssetsPending(state).done).toBe(0);

    resolve();
    await p;
    expect(getCriticalGltfLoadCount()).toBe(0);
    expect(gltfAssetsReady(state)).toBe(true);
    expect(describeGltfAssetsPending(state).done).toBe(1);
    expect(describeGltfAssetsPending(state).total).toBe(1);
    expect(describeGltfAssetsPending(state).remaining).toBe(0);
  });
});
