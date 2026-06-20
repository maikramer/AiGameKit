import { describe, expect, it } from 'bun:test';
import {
  clearGltfMasterCache,
  evictGltfMaster,
  getActiveGltfLoadCount,
} from 'vibegame';
import { hasAnyGltfLoadStarted } from '../../../src/extras/gltf-bridge';

describe('GLTF master cache API surface (M3)', () => {
  it('clearGltfMasterCache returns a non-negative count and empties the cache', () => {
    const removed = clearGltfMasterCache();

    expect(typeof removed).toBe('number');
    expect(removed).toBeGreaterThanOrEqual(0);

    expect(clearGltfMasterCache()).toBe(0);
  });

  it('evictGltfMaster returns false for a URL that was never cached', () => {
    clearGltfMasterCache();

    expect(evictGltfMaster('nonexistent')).toBe(false);
  });

  it('evictGltfMaster on distinct URLs never throws and stays boolean', () => {
    clearGltfMasterCache();

    for (const url of ['a.glb', 'b.glb', 'http://x/y.glb']) {
      expect(typeof evictGltfMaster(url)).toBe('boolean');
    }
  });

  it('tracks whether any GLTF load has ever started', () => {
    expect(typeof hasAnyGltfLoadStarted()).toBe('boolean');
    expect(typeof getActiveGltfLoadCount()).toBe('number');
    expect(getActiveGltfLoadCount()).toBeGreaterThanOrEqual(0);
  });
});
