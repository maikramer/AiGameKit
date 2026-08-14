import { afterEach, beforeEach, describe, expect, it } from 'bun:test';
import {
  colliderMeshFailed,
  meshRetryDelayMs,
  requestColliderMesh,
  resetColliderMeshCacheForTests,
} from '../../../../src/plugins/physics/mesh-collider';
import {
  resetResilientNetForTests,
  resilientNetConfig,
} from '../../../../src/core/utils/resilient-net';

/**
 * Minimal valid GLB: one float32 triangle with u16 indices — enough for the
 * manual collision parser to produce a ready cache entry.
 */
function buildTriangleGlb(): ArrayBuffer {
  const positions = new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]);
  const indices = new Uint16Array([0, 1, 2]);
  const binLength = positions.byteLength + indices.byteLength;
  const binPadded = Math.ceil(binLength / 4) * 4;
  const bin = new Uint8Array(binPadded);
  bin.set(new Uint8Array(positions.buffer), 0);
  bin.set(new Uint8Array(indices.buffer), positions.byteLength);

  const json = JSON.stringify({
    asset: { version: '2.0' },
    scene: 0,
    scenes: [{ nodes: [0] }],
    nodes: [{ mesh: 0 }],
    meshes: [{ primitives: [{ attributes: { POSITION: 0 }, indices: 1 }] }],
    accessors: [
      { bufferView: 0, componentType: 5126, count: 3, type: 'VEC3' },
      { bufferView: 1, componentType: 5123, count: 3, type: 'SCALAR' },
    ],
    bufferViews: [
      { buffer: 0, byteOffset: 0, byteLength: positions.byteLength },
      {
        buffer: 0,
        byteOffset: positions.byteLength,
        byteLength: indices.byteLength,
      },
    ],
    buffers: [{ byteLength: binLength }],
  });
  const jsonBytes = new TextEncoder().encode(json);
  const jsonPadded = Math.ceil(jsonBytes.length / 4) * 4;
  const jsonChunk = new Uint8Array(jsonPadded).fill(0x20);
  jsonChunk.set(jsonBytes);

  const total = 12 + 8 + jsonPadded + 8 + binPadded;
  const buf = new ArrayBuffer(total);
  const view = new DataView(buf);
  const bytes = new Uint8Array(buf);
  view.setUint32(0, 0x46546c67, true);
  view.setUint32(4, 2, true);
  view.setUint32(8, total, true);
  view.setUint32(12, jsonPadded, true);
  view.setUint32(16, 0x4e4f534a, true);
  bytes.set(jsonChunk, 20);
  const binStart = 20 + jsonPadded;
  view.setUint32(binStart, binPadded, true);
  view.setUint32(binStart + 4, 0x004e4942, true);
  bytes.set(bin, binStart + 8);
  return buf;
}

describe('mesh-collider resilience', () => {
  let realFetch: typeof fetch;
  let realNow: () => number;

  beforeEach(() => {
    realFetch = globalThis.fetch;
    realNow = Date.now;
    resetColliderMeshCacheForTests();
    resetResilientNetForTests();
    resilientNetConfig.retries = 0;
    resilientNetConfig.baseDelayMs = 1;
  });

  afterEach(() => {
    globalThis.fetch = realFetch;
    Date.now = realNow;
    resetColliderMeshCacheForTests();
    resetResilientNetForTests();
  });

  it('a 404 is permanent: collider gives up without refetching', async () => {
    let fetches = 0;
    globalThis.fetch = (async () => {
      fetches += 1;
      return new Response('missing', { status: 404 });
    }) as unknown as typeof fetch;

    expect(requestColliderMesh('/assets/bad_collision.glb')).toBeNull();
    await new Promise((r) => setTimeout(r, 5));
    expect(colliderMeshFailed('/assets/bad_collision.glb')).toBe(true);
    expect(requestColliderMesh('/assets/bad_collision.glb')).toBeNull();
    expect(fetches).toBe(1);
  });

  it('a 500 is transient: not failed, retried after the backoff window', async () => {
    let fetches = 0;
    let status = 500;
    globalThis.fetch = (async () => {
      fetches += 1;
      if (status === 500) return new Response('boom', { status: 500 });
      return new Response(buildTriangleGlb(), { status: 200 });
    }) as unknown as typeof fetch;

    expect(requestColliderMesh('/assets/flaky_collision.glb')).toBeNull();
    await new Promise((r) => setTimeout(r, 5));
    expect(colliderMeshFailed('/assets/flaky_collision.glb')).toBe(false);
    expect(requestColliderMesh('/assets/flaky_collision.glb')).toBeNull();
    expect(fetches).toBe(1); // still inside the backoff window

    // Jump past the retry deadline and let the origin recover.
    status = 200;
    Date.now = () => realNow() + meshRetryDelayMs(1) + 1;
    expect(requestColliderMesh('/assets/flaky_collision.glb')).toBeNull();
    await new Promise((r) => setTimeout(r, 5));
    const data = requestColliderMesh('/assets/flaky_collision.glb');
    expect(data).not.toBeNull();
    expect(fetches).toBe(2);
    expect(colliderMeshFailed('/assets/flaky_collision.glb')).toBe(false);
  });

  it('caches a successful load — one fetch serves every later request', async () => {
    let fetches = 0;
    globalThis.fetch = (async () => {
      fetches += 1;
      return new Response(buildTriangleGlb(), { status: 200 });
    }) as unknown as typeof fetch;

    expect(requestColliderMesh('/assets/ok_collision.glb')).toBeNull();
    await new Promise((r) => setTimeout(r, 5));
    const first = requestColliderMesh('/assets/ok_collision.glb');
    const second = requestColliderMesh('/assets/ok_collision.glb');
    expect(first).not.toBeNull();
    expect(second).toBe(first);
    expect(fetches).toBe(1);
  });

  it('retry delays escalate and cap out', () => {
    expect(meshRetryDelayMs(0)).toBe(2_000);
    expect(meshRetryDelayMs(1)).toBe(4_000);
    expect(meshRetryDelayMs(2)).toBe(8_000);
    expect(meshRetryDelayMs(10)).toBe(30_000);
  });
});
