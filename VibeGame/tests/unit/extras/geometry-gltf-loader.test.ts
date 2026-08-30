import { describe, expect, it } from 'bun:test';
import { createGLTFLoader, createGeometryGLTFLoader } from 'aigamekit-vibegame';

/**
 * Minimal GLB with one triangle whose material points at a KTX2 (basisu)
 * texture, declared in `extensionsRequired`. Loading it with a plain
 * GLTFLoader that has no KTX2Loader attached is a hard failure — the shape a
 * collision GLB from the asset pipeline (KTX2 on by default) takes.
 */
function buildKtx2TexturedGlb(): ArrayBuffer {
  const positions = new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]);
  const indices = new Uint16Array([0, 1, 2]);
  const fakeKtx2 = new Uint8Array([0xab, 0x4b, 0x54, 0x58, 0x20, 0x32, 0x30]);

  const binLength =
    positions.byteLength + indices.byteLength + fakeKtx2.byteLength;
  const binPadded = Math.ceil(binLength / 4) * 4;
  const bin = new Uint8Array(binPadded);
  bin.set(new Uint8Array(positions.buffer), 0);
  bin.set(new Uint8Array(indices.buffer), positions.byteLength);
  bin.set(fakeKtx2, positions.byteLength + indices.byteLength);

  const json = JSON.stringify({
    asset: { version: '2.0' },
    extensionsUsed: ['KHR_texture_basisu'],
    extensionsRequired: ['KHR_texture_basisu'],
    scene: 0,
    scenes: [{ nodes: [0] }],
    nodes: [{ mesh: 0 }],
    meshes: [
      {
        primitives: [{ attributes: { POSITION: 0 }, indices: 1, material: 0 }],
      },
    ],
    materials: [{ pbrMetallicRoughness: { baseColorTexture: { index: 0 } } }],
    textures: [{ extensions: { KHR_texture_basisu: { source: 0 } } }],
    images: [{ bufferView: 2, mimeType: 'image/ktx2' }],
    accessors: [
      {
        bufferView: 0,
        componentType: 5126,
        count: 3,
        type: 'VEC3',
        min: [0, 0, 0],
        max: [1, 1, 0],
      },
      { bufferView: 1, componentType: 5123, count: 3, type: 'SCALAR' },
    ],
    bufferViews: [
      { buffer: 0, byteOffset: 0, byteLength: positions.byteLength },
      {
        buffer: 0,
        byteOffset: positions.byteLength,
        byteLength: indices.byteLength,
      },
      {
        buffer: 0,
        byteOffset: positions.byteLength + indices.byteLength,
        byteLength: fakeKtx2.byteLength,
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

  view.setUint32(0, 0x46546c67, true); // 'glTF'
  view.setUint32(4, 2, true);
  view.setUint32(8, total, true);
  view.setUint32(12, jsonPadded, true);
  view.setUint32(16, 0x4e4f534a, true); // 'JSON'
  bytes.set(jsonChunk, 20);
  view.setUint32(20 + jsonPadded, binPadded, true);
  view.setUint32(24 + jsonPadded, 0x004e4942, true); // 'BIN'
  bytes.set(bin, 28 + jsonPadded);
  return buf;
}

describe('createGeometryGLTFLoader', () => {
  it('parses a KTX2-textured GLB that the plain loader rejects', async () => {
    const glb = buildKtx2TexturedGlb();

    await expect(createGLTFLoader().parseAsync(glb, '')).rejects.toThrow(
      /setKTX2Loader/
    );

    const gltf = await createGeometryGLTFLoader().parseAsync(glb, '');
    let meshes = 0;
    let vertices = 0;
    gltf.scene.traverse((obj) => {
      const mesh = obj as { isMesh?: boolean; geometry?: never };
      if (!mesh.isMesh) return;
      meshes++;
      const geom = (
        obj as unknown as {
          geometry: { attributes: Record<string, { count: number }> };
        }
      ).geometry;
      vertices += geom.attributes.position?.count ?? 0;
    });
    expect(meshes).toBe(1);
    expect(vertices).toBe(3);
  });

  it('leaves the geometry usable without loading any texture', async () => {
    const gltf = await createGeometryGLTFLoader().parseAsync(
      buildKtx2TexturedGlb(),
      ''
    );
    const mesh = gltf.scene.children[0] as unknown as {
      material: { map: unknown };
    };
    expect(mesh.material.map).toBeFalsy();
  });
});
