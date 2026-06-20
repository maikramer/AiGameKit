import { describe, expect, it } from 'bun:test';
import { Document, NodeIO } from '@gltf-transform/core';
import { validateGltf } from 'vibegame';

const textEncoder = new TextEncoder();

function toArrayBuffer(u8: Uint8Array): ArrayBuffer {
  const ab = new ArrayBuffer(u8.byteLength);
  new Uint8Array(ab).set(u8);
  return ab;
}

function u32(bytes: number[], value: number): void {
  bytes.push(
    value & 0xff,
    (value >>> 8) & 0xff,
    (value >>> 16) & 0xff,
    (value >>> 24) & 0xff
  );
}

/** Pack a JS object into a GLB binary container (JSON chunk only, space-padded to 4 bytes). */
function packGlb(json: unknown): Uint8Array {
  const jsonBytes = textEncoder.encode(JSON.stringify(json));
  const padLen = (4 - (jsonBytes.length % 4)) % 4;
  const padded = new Uint8Array(jsonBytes.length + padLen);
  padded.set(jsonBytes, 0);
  for (let i = 0; i < padLen; i++) padded[jsonBytes.length + i] = 0x20;

  const headerSize = 12;
  const jsonChunkHeader = 8;
  const totalLength = headerSize + jsonChunkHeader + padded.length;

  const out: number[] = [];
  u32(out, 0x46546c67); // "glTF"
  u32(out, 2); // version
  u32(out, totalLength);
  u32(out, padded.length); // JSON chunk length
  u32(out, 0x4e4f534a); // "JSON"
  for (let i = 0; i < padded.length; i++) out.push(padded[i]);

  return new Uint8Array(out);
}

/** Minimal spec-valid glTF: asset 2.0, one scene with a single named node (no geometry). */
function validGlbBytes(): Promise<Uint8Array> {
  const doc = new Document();
  const root = doc.getRoot();
  const scene = doc.createScene('MainScene');
  const node = doc.createNode('RootNode');
  scene.addChild(node);
  void root;
  return new NodeIO().writeBinary(doc);
}

describe('validateGltf', () => {
  it('accepts a minimal valid GLB with no errors', async () => {
    const bytes = await validGlbBytes();
    const report = await validateGltf(toArrayBuffer(bytes));

    expect(report.valid).toBe(true);
    expect(report.errors).toEqual([]);
    expect(report.mimeType).toBe('model/gltf-binary');
    expect(report.byteLength).toBe(bytes.byteLength);
  });

  it('also accepts a Uint8Array view', async () => {
    const bytes = await validGlbBytes();
    const report = await validateGltf(bytes);

    expect(report.valid).toBe(true);
    expect(report.errors).toEqual([]);
  });

  it('flags a GLB whose JSON is missing asset.version', async () => {
    const broken = packGlb({
      scene: 0,
      scenes: [{ nodes: [0] }],
      nodes: [{ name: 'N' }],
    });
    const report = await validateGltf(toArrayBuffer(broken));

    expect(report.valid).toBe(false);
    const codes = report.errors.map((e) => e.code);
    expect(codes).toContain('ASSET_VERSION_MISSING');
    const issue = report.errors.find((e) => e.code === 'ASSET_VERSION_MISSING');
    expect(issue?.pointer).toBe('/asset/version');
  });

  it('flags a malformed asset.version string', async () => {
    const broken = packGlb({ asset: { version: 'two-point-oh' } });
    const report = await validateGltf(toArrayBuffer(broken));

    expect(report.valid).toBe(false);
    expect(report.errors.map((e) => e.code)).toContain('ASSET_VERSION_FORMAT');
  });

  it('flags a bufferView referencing a missing buffer', async () => {
    const broken = packGlb({
      asset: { version: '2.0' },
      scenes: [{ nodes: [0] }],
      nodes: [{}],
      bufferViews: [{ buffer: 3, byteLength: 4 }],
    });
    const report = await validateGltf(toArrayBuffer(broken));

    expect(report.valid).toBe(false);
    expect(report.errors.map((e) => e.code)).toContain(
      'BUFFERVIEW_BUFFER_OUT_OF_RANGE'
    );
  });

  it('falls back to bare glTF JSON text and still validates', async () => {
    const brokenJson = new TextEncoder().encode(
      JSON.stringify({ meshes: [{ primitives: [{ attributes: {} }] }] })
    );
    const report = await validateGltf(toArrayBuffer(brokenJson));

    expect(report.mimeType).toBe('model/gltf+json');
    expect(report.valid).toBe(false);
    const codes = report.errors.map((e) => e.code);
    expect(codes).toContain('ASSET_VERSION_MISSING');
  });

  it('reports a parse failure for garbage bytes', async () => {
    const garbage = new Uint8Array([
      0xff, 0x00, 0xfe, 0x01, 0x02, 0x03, 0x04, 0x05,
    ]);
    const report = await validateGltf(toArrayBuffer(garbage));

    expect(report.valid).toBe(false);
    expect(report.errors.map((e) => e.code)).toContain('GLTF_PARSE_FAILED');
  });
});
