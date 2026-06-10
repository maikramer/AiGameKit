import { describe, expect, it } from 'bun:test';
import {
  MeshAnchor,
  buildMeshColliderGeometry,
  parseGlbCollisionMesh,
} from 'vibegame/physics';

/**
 * Builds a minimal valid GLB: one triangle (0,0,0)/(1,0,0)/(0,1,0) with u16
 * indices, inside a node with `translation` and `scale` — enough to prove the
 * parser applies node transforms and decodes both chunks.
 */
function buildTriangleGlb(
  translation: [number, number, number],
  scale: [number, number, number]
): ArrayBuffer {
  const positions = new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]);
  const indices = new Uint16Array([0, 1, 2]);

  const binLength = positions.byteLength + indices.byteLength; // 42
  const binPadded = Math.ceil(binLength / 4) * 4;
  const bin = new Uint8Array(binPadded);
  bin.set(new Uint8Array(positions.buffer), 0);
  bin.set(new Uint8Array(indices.buffer), positions.byteLength);

  const json = JSON.stringify({
    asset: { version: '2.0' },
    scene: 0,
    scenes: [{ nodes: [0] }],
    nodes: [{ mesh: 0, translation, scale }],
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
  const jsonChunk = new Uint8Array(jsonPadded).fill(0x20); // pad with spaces
  jsonChunk.set(jsonBytes);

  const total = 12 + 8 + jsonPadded + 8 + binPadded;
  const buf = new ArrayBuffer(total);
  const view = new DataView(buf);
  const bytes = new Uint8Array(buf);

  view.setUint32(0, 0x46546c67, true); // 'glTF'
  view.setUint32(4, 2, true);
  view.setUint32(8, total, true);

  view.setUint32(12, jsonPadded, true);
  view.setUint32(16, 0x4e4f534a, true); // 'JSON'
  bytes.set(jsonChunk, 20);

  const binStart = 20 + jsonPadded;
  view.setUint32(binStart, binPadded, true);
  view.setUint32(binStart + 4, 0x004e4942, true); // 'BIN\0'
  bytes.set(bin, binStart + 8);

  return buf;
}

describe('parseGlbCollisionMesh', () => {
  it('decodes positions with node TRS applied', () => {
    const glb = buildTriangleGlb([10, 5, 0], [2, 2, 2]);
    const mesh = parseGlbCollisionMesh(glb);

    expect(mesh.indices).toEqual(new Uint32Array([0, 1, 2]));
    expect(Array.from(mesh.vertices)).toEqual([
      10,
      5,
      0, // (0,0,0) * 2 + (10,5,0)
      12,
      5,
      0, // (1,0,0) * 2 + (10,5,0)
      10,
      7,
      0, // (0,1,0) * 2 + (10,5,0)
    ]);
  });

  it('rejects buffers that are not GLB', () => {
    const junk = new TextEncoder().encode('<!DOCTYPE html>not a glb').buffer;
    expect(() => parseGlbCollisionMesh(junk as ArrayBuffer)).toThrow(
      'bad magic'
    );
  });
});

describe('buildMeshColliderGeometry', () => {
  it('scales vertices uniformly and leaves source data untouched', () => {
    const src = {
      vertices: new Float32Array([1, 2, 3]),
      indices: new Uint32Array([0]),
    };
    const out = buildMeshColliderGeometry(src, 2, MeshAnchor.None);
    expect(Array.from(out.vertices)).toEqual([2, 4, 6]);
    expect(Array.from(src.vertices)).toEqual([1, 2, 3]);
  });

  it('anchor "base" recenters the AABB base center onto the origin', () => {
    const glb = buildTriangleGlb([10, 5, 0], [1, 1, 1]);
    const mesh = parseGlbCollisionMesh(glb);
    const out = buildMeshColliderGeometry(mesh, 1, MeshAnchor.Base);

    // x spans 10..11 → recentered to -0.5..0.5; y min 5 → 0; z stays 0.
    expect(Array.from(out.vertices)).toEqual([
      -0.5, 0, 0, 0.5, 0, 0, -0.5, 1, 0,
    ]);
  });
});
