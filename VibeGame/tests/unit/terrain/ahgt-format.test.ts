import { describe, expect, it } from 'bun:test';
import {
  AHGT_MAGIC,
  AHGT_VERSION,
  parseAhgt,
  serializeAhgt,
} from '../../../src/plugins/terrain/ahgt-format';
import type { HeightSampler } from '../../../src/plugins/terrain/height-sampler';
import { sampleHeightAt } from '../../../src/plugins/terrain/height-sampler';

/** Gentle radial bump so quantization round-trip is non-trivial. */
function bumpSampler(
  size: number,
  worldSize: number,
  maxHeight: number
): HeightSampler {
  const data = new Float32Array(size * size);
  const half = worldSize / 2;
  const step = worldSize / (size - 1);
  for (let z = 0; z < size; z++) {
    for (let x = 0; x < size; x++) {
      const wx = x * step - half;
      const wz = z * step - half;
      const r = Math.hypot(wx, wz) / half;
      data[z * size + x] = Math.max(0, 1 - r) * 0.7;
    }
  }
  return { width: size, height: size, data, worldSize, maxHeight };
}

describe('ahgt header', () => {
  it('writes the AHGT magic and version at the expected offsets', () => {
    const bytes = serializeAhgt(bumpSampler(8, 100, 50));
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    expect(view.getUint32(0, true)).toBe(AHGT_MAGIC);
    expect(view.getUint16(4, true)).toBe(AHGT_VERSION);
  });

  it('writes the grid width and height at offsets 6 and 8', () => {
    const bytes = serializeAhgt(bumpSampler(8, 100, 50));
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    expect(view.getUint16(6, true)).toBe(8);
    expect(view.getUint16(8, true)).toBe(8);
  });
});

describe('ahgt round-trip', () => {
  it('serializes and parses back to an equivalent sampler (uint16 precision)', () => {
    const original = bumpSampler(64, 200, 50);
    const bytes = serializeAhgt(original);
    const { sampler, meta } = parseAhgt(bytes);
    expect(sampler.width).toBe(original.width);
    expect(sampler.height).toBe(original.height);
    expect(sampler.worldSize).toBe(original.worldSize);
    expect(sampler.maxHeight).toBe(original.maxHeight);
    expect(meta.worldSize).toBe(original.worldSize);

    // Heights match within uint16 quantization step (maxHeight/65535) plus a
    // tiny float epsilon for the bilinear read.
    const eps = original.maxHeight / 65535 + 1e-6;
    for (const [x, z] of [
      [0, 0],
      [50, 0],
      [0, 50],
      [50, 50],
      [-30, 20],
    ]) {
      const before = sampleHeightAt(original, x, z);
      const after = sampleHeightAt(sampler, x, z);
      expect(Math.abs(before - after)).toBeLessThan(eps);
    }
  });

  it('round-trips a subarray view (non-zero byteOffset) without corruption', () => {
    // Pack the ahgt blob into a larger buffer with a non-zero offset — this is
    // the DataView byteOffset pitfall the parser must handle.
    const original = bumpSampler(8, 100, 50);
    const bytes = serializeAhgt(original);
    const padded = new Uint8Array(bytes.byteLength + 7);
    padded.set(bytes, 7);
    const view = padded.subarray(7);
    const { sampler } = parseAhgt(view);
    expect(sampler.width).toBe(original.width);
    expect(
      Math.abs(sampleHeightAt(sampler, 0, 0) - sampleHeightAt(original, 0, 0))
    ).toBeLessThan(original.maxHeight / 65535 + 1e-6);
  });

  it('deflate actually compresses smooth terrain below raw uint16 size', () => {
    const sampler = bumpSampler(64, 200, 50);
    const bytes = serializeAhgt(sampler);
    const rawUint16 = 64 * 64 * 2;
    expect(bytes.byteLength).toBeLessThan(rawUint16);
  });

  it('parses a flat (dataless) sampler back to a flat sampler', () => {
    const flat: HeightSampler = {
      width: 1,
      height: 1,
      data: null,
      worldSize: 100,
      maxHeight: 10,
    };
    const bytes = serializeAhgt(flat);
    const { sampler } = parseAhgt(bytes);
    // dataless path → sampleHeightAt returns 0.
    expect(sampleHeightAt(sampler, 0, 0)).toBe(0);
    expect(sampler.data).toBeNull();
  });
});

describe('ahgt error handling', () => {
  it('throws on bad magic', () => {
    const bad = new Uint8Array(20);
    new DataView(bad.buffer).setUint32(0, 0xdeadbeef, true);
    expect(() => parseAhgt(bad)).toThrow(/magic|AHGT/i);
  });

  it('throws on a truncated header', () => {
    expect(() => parseAhgt(new Uint8Array(8))).toThrow(/truncated|header/i);
  });

  it('throws on an unsupported version', () => {
    const bytes = serializeAhgt(bumpSampler(4, 100, 50));
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    view.setUint16(4, 999, true); // bogus version
    expect(() => parseAhgt(bytes)).toThrow(/version/i);
  });
});
