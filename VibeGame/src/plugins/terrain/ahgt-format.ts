import { deflateSync, inflateSync } from 'fflate';
import type { HeightSampler } from './height-sampler';

/** AHGT magic, "AHGT" little-endian (0x41 0x48 0x47 0x54 → 0x54474841). */
export const AHGT_MAGIC = 0x54474841;
export const AHGT_VERSION = 1;

export interface AhgtMeta {
  worldSize: number;
  maxHeight: number;
  originX: number;
  originZ: number;
}

/**
 * Layout: 16-byte binary header, u32 metadata length, UTF-8 JSON metadata,
 * deflate-compressed uint16 height grid.
 *
 *   offset 0  u32  magic "AHGT"
 *   offset 4  u16  version
 *   offset 6  u16  grid width in samples
 *   offset 8  u16  grid height in samples
 *   offset 10 u16  flags (reserved, 0)
 *   offset 12 u32  reserved padding
 */
const HEADER_BYTES = 16;

/**
 * Serialize a HeightSampler to a compact `.ahgt` blob. Heights are quantized
 * to uint16 over [0, maxHeight]: precision ~maxHeight/65535 (3mm over 200m),
 * far better than PNG uint8 grayscale (~0.78m over 200m). A flat (dataless)
 * sampler serializes as a zero grid and parses back to a flat sampler.
 */
export function serializeAhgt(
  sampler: HeightSampler,
  metaOverrides: Partial<AhgtMeta> = {}
): Uint8Array {
  const width = Math.max(1, Math.min(65535, sampler.width));
  const height = Math.max(1, Math.min(65535, sampler.height));
  const quantized = new Uint16Array(width * height);
  const { data } = sampler;
  if (data) {
    const n = Math.min(quantized.length, data.length);
    for (let i = 0; i < n; i++) {
      const normalized = Math.max(0, Math.min(1, data[i]!));
      quantized[i] = Math.round(normalized * 65535);
    }
  }
  const rawHeightBytes = new Uint8Array(
    quantized.buffer,
    quantized.byteOffset,
    quantized.byteLength
  );
  const compressed = deflateSync(rawHeightBytes, { level: 6 });

  const meta: AhgtMeta = {
    worldSize: sampler.worldSize,
    maxHeight: sampler.maxHeight,
    originX: metaOverrides.originX ?? 0,
    originZ: metaOverrides.originZ ?? 0,
  };
  const metaJson = new TextEncoder().encode(JSON.stringify(meta));

  const total = HEADER_BYTES + 4 + metaJson.byteLength + compressed.byteLength;
  const out = new Uint8Array(total);
  const view = new DataView(out.buffer);
  view.setUint32(0, AHGT_MAGIC, true);
  view.setUint16(4, AHGT_VERSION, true);
  view.setUint16(6, width, true);
  view.setUint16(8, height, true);
  view.setUint16(10, 0, true); // flags
  view.setUint32(12, 0, true); // reserved
  view.setUint32(HEADER_BYTES, metaJson.byteLength, true);
  out.set(metaJson, HEADER_BYTES + 4);
  out.set(compressed, HEADER_BYTES + 4 + metaJson.byteLength);
  return out;
}

/** Parse an `.ahgt` blob back into a HeightSampler + metadata. */
export function parseAhgt(bytes: Uint8Array): {
  sampler: HeightSampler;
  meta: AhgtMeta;
} {
  if (bytes.byteLength < HEADER_BYTES + 4) {
    throw new Error('AHGT: truncated header');
  }
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const magic = view.getUint32(0, true);
  if (magic !== AHGT_MAGIC) {
    throw new Error(`AHGT: bad magic 0x${magic.toString(16)} (expected AHGT)`);
  }
  const version = view.getUint16(4, true);
  if (version !== AHGT_VERSION) {
    throw new Error(`AHGT: unsupported version ${version}`);
  }
  const width = view.getUint16(6, true);
  const height = view.getUint16(8, true);
  const metaLen = view.getUint32(HEADER_BYTES, true);
  const metaStart = HEADER_BYTES + 4;
  if (metaStart + metaLen > bytes.byteLength) {
    throw new Error('AHGT: truncated metadata block');
  }
  let meta: AhgtMeta;
  try {
    meta = JSON.parse(
      new TextDecoder().decode(bytes.subarray(metaStart, metaStart + metaLen))
    ) as AhgtMeta;
  } catch (err) {
    throw new Error(
      `AHGT: invalid metadata JSON — ${err instanceof Error ? err.message : err}`,
      { cause: err }
    );
  }

  // A 1×1 grid is a flat sampler: parse back to data:null so height queries
  // take the flat path instead of interpolating a degenerate grid.
  if (width < 2 || height < 2) {
    return {
      sampler: {
        width: 1,
        height: 1,
        data: null,
        worldSize: meta.worldSize,
        maxHeight: meta.maxHeight,
      },
      meta,
    };
  }

  const raw = inflateSync(bytes.subarray(metaStart + metaLen));
  if (raw.byteLength < width * height * 2) {
    throw new Error(
      `AHGT: height payload too small (${raw.byteLength} bytes for ${width}x${height})`
    );
  }
  // inflateSync returns a fresh, offset-0 buffer, so the uint16 view is aligned.
  const quantized = new Uint16Array(raw.buffer, raw.byteOffset, width * height);

  const data = new Float32Array(width * height);
  for (let i = 0; i < data.length; i++) {
    data[i] = quantized[i]! / 65535;
  }

  return {
    sampler: {
      width,
      height,
      data,
      worldSize: meta.worldSize,
      maxHeight: meta.maxHeight,
    },
    meta,
  };
}
