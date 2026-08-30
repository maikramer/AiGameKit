import { afterEach, describe, expect, it } from 'bun:test';
import {
  clearGltfMasterCache,
  evictGltfMaster,
  getActiveGltfLoadCount,
  loadGltfMasterTracked,
  loadSettledGltfMaster,
  State,
} from 'aigamekit-vibegame';
import { hasAnyGltfLoadStarted } from '../../../src/extras/gltf-bridge';

/** Minimal valid GLB (one empty scene) so the loader parses without network. */
function tinyGlb(): Uint8Array {
  const json = JSON.stringify({
    asset: { version: '2.0' },
    scene: 0,
    scenes: [{ nodes: [] }],
  });
  const jsonBytes = new TextEncoder().encode(json);
  const pad = (4 - (jsonBytes.length % 4)) % 4;
  const total = 12 + 8 + jsonBytes.length + pad;
  const buf = new ArrayBuffer(total);
  const view = new DataView(buf);
  view.setUint32(0, 0x46546c67, true); // 'glTF'
  view.setUint32(4, 2, true);
  view.setUint32(8, total, true);
  view.setUint32(12, jsonBytes.length + pad, true);
  view.setUint32(16, 0x4e4f534a, true); // 'JSON'
  const body = new Uint8Array(buf, 20);
  body.set(jsonBytes);
  body.fill(0x20, jsonBytes.length); // spec: JSON chunk pads with spaces
  return body;
}

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

describe('loadSettledGltfMaster', () => {
  afterEach(() => {
    clearGltfMasterCache();
  });

  // three's FileLoader emits ProgressEvent while streaming a response —
  // DOM-less runtimes (bun test) need a stub for the parse to complete.
  globalThis.ProgressEvent ??= class {
    type: string;
    constructor(type: string, init?: Record<string, unknown>) {
      this.type = type;
      Object.assign(this, init);
    }
  } as unknown as typeof ProgressEvent;

  const dataUrl = (): string =>
    'data:model/gltf-binary;base64,' +
    Buffer.from(tinyGlb()).toString('base64');

  it('returns null for a URL that was never loaded', () => {
    clearGltfMasterCache();
    expect(loadSettledGltfMaster('https://unit.test/never.glb')).toBeNull();
  });

  it('returns the parsed master once its load has settled', async () => {
    const url = dataUrl();
    await loadGltfMasterTracked(new State(), url, 'background');

    const peeked = loadSettledGltfMaster(url);
    expect(peeked).not.toBeNull();
    const gltf = await peeked!;
    expect(Array.isArray(gltf.animations)).toBe(true);
    expect(Array.isArray(gltf.scene.children)).toBe(true);
  });

  it('returns null again after the master is evicted', async () => {
    const url = dataUrl();
    await loadGltfMasterTracked(new State(), url, 'background');
    expect(loadSettledGltfMaster(url)).not.toBeNull();

    evictGltfMaster(url);
    expect(loadSettledGltfMaster(url)).toBeNull();
  });
});
