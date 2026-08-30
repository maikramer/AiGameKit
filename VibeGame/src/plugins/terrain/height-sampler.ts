import type { State } from '../../core';
import { fetchBlobResilient } from '../../core/utils/resilient-net';
import { Terrain } from './components';
import { meshSurfaceResolutionForPoint } from './lod-select';
import { getTerrainContext } from './utils';
import type { TerrainEntityData } from './utils';

/**
 * CPU-side height sampler for a terrain field.
 *
 * Source of truth for terrain elevation, shared by mesh generation, physics
 * heightfields, and gameplay height queries. A flat sampler (no heightmap) is
 * the F1 baseline; F2 fills `heights` from a decoded heightmap image.
 */
export interface HeightSampler {
  /** Heightmap grid width in samples (1 for a flat field). */
  width: number;
  /** Heightmap grid height in samples (1 for a flat field). */
  height: number;
  /** Normalized [0,1] elevation per sample, row-major. Empty when flat. */
  data: Float32Array | null;
  /** World extent (X and Z) the samples are stretched across. */
  worldSize: number;
  /** Elevation in world units at normalized [0,1] amplitude. */
  maxHeight: number;
  /**
   * 0 = bilinear taps, 1 = Catmull-Rom. Bilinear is C0 but not C1: the
   * derivative jumps at every texel boundary, so a slope reads as a grid of
   * flat facets the size of one texel (2 m on a 2048 map over 4 km, which is
   * plainly visible from the ground). Catmull-Rom is C1, so the normals run
   * continuously across cells and the slope reads smooth. Fed from the
   * `height-smoothing` attribute of `<Terrain>`; defaults to fully smooth.
   */
  smoothing?: number;
}

export interface HeightSamplerData {
  width: number;
  height: number;
  data: Float32Array;
}

export function createFlatSampler(
  worldSize: number,
  maxHeight: number
): HeightSampler {
  return { width: 1, height: 1, data: null, worldSize, maxHeight };
}

export function createHeightmapSampler(
  worldSize: number,
  maxHeight: number,
  imgData: HeightSamplerData
): HeightSampler {
  return {
    width: imgData.width,
    height: imgData.height,
    data: imgData.data,
    worldSize,
    maxHeight,
  };
}

interface DecodedImage {
  width: number;
  height: number;
  source: CanvasImageSource;
  close(): void;
}

/**
 * Decode an image blob into a drawable source.
 *
 * Prefers `createImageBitmap` (works in workers), but falls back to an
 * `HTMLImageElement` when it is unavailable — e.g. Firefox builds where
 * `createImageBitmap` is not exposed. Without this fallback the heightmap
 * fails to decode and the terrain stays flat.
 */
async function decodeImageBlob(blob: Blob): Promise<DecodedImage> {
  if (typeof createImageBitmap === 'function') {
    const bitmap = await createImageBitmap(blob);
    return {
      width: bitmap.width,
      height: bitmap.height,
      source: bitmap,
      close: () => bitmap.close(),
    };
  }

  if (typeof Image === 'undefined' || typeof URL === 'undefined') {
    throw new Error(
      'No image decoder available (no createImageBitmap / Image)'
    );
  }

  const objectUrl = URL.createObjectURL(blob);
  try {
    const img = new Image();
    img.src = objectUrl;
    if (typeof img.decode === 'function') {
      await img.decode();
    } else {
      await new Promise<void>((resolve, reject) => {
        img.onload = () => resolve();
        img.onerror = () => reject(new Error('Image load failed'));
      });
    }
    return {
      width: img.naturalWidth,
      height: img.naturalHeight,
      source: img,
      close: () => URL.revokeObjectURL(objectUrl),
    };
  } catch (e) {
    URL.revokeObjectURL(objectUrl);
    throw e;
  }
}

export async function loadHeightmapFromUrl(
  url: string
): Promise<HeightSamplerData> {
  let blob: Blob;
  try {
    blob = await fetchBlobResilient(url);
  } catch (e) {
    throw new Error(`Heightmap fetch failed: ${url} — ${e}`, { cause: e });
  }
  return decodeHeightmapBlob(blob, url);
}

// Guards against memory bombs: a hostile or accidental 20000×20000 PNG would
// otherwise allocate a 1.6 GB Float32Array and take the tab down with it.
// 8192 is the legitimate ceiling (doubled 4 km heightmap ⇒ 0.5 m/px): its
// Float32Array is 268 MB + transient decode buffers, desktop-class only.
const MAX_HEIGHTMAP_SIDE = 8192;
const MAX_HEIGHTMAP_PIXELS = MAX_HEIGHTMAP_SIDE ** 2;

export async function decodeHeightmapBlob(
  blob: Blob,
  url = 'heightmap'
): Promise<HeightSamplerData> {
  let image: DecodedImage;
  try {
    image = await decodeImageBlob(blob);
  } catch (e) {
    throw new Error(
      `Heightmap decode failed (${blob.type}, ${blob.size}B): ${e}`,
      { cause: e }
    );
  }

  if (
    image.width <= 0 ||
    image.height <= 0 ||
    image.width > MAX_HEIGHTMAP_SIDE ||
    image.height > MAX_HEIGHTMAP_SIDE ||
    image.width * image.height > MAX_HEIGHTMAP_PIXELS
  ) {
    image.close();
    throw new Error(
      `Heightmap ${url} is ${image.width}x${image.height}; each side must be in [1, ${MAX_HEIGHTMAP_SIDE}]`
    );
  }

  const canvas =
    typeof OffscreenCanvas !== 'undefined'
      ? new OffscreenCanvas(image.width, image.height)
      : (() => {
          const c = document.createElement('canvas');
          c.width = image.width;
          c.height = image.height;
          return c;
        })();

  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  if (!ctx) {
    image.close();
    throw new Error(`Heightmap ${url}: 2D canvas context unavailable`);
  }
  ctx.drawImage(image.source as CanvasImageSource, 0, 0);
  image.close();

  const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
  const pixels = imageData.data;
  const data = new Float32Array(canvas.width * canvas.height);

  // 16-bit height packed as R = high byte, G = low byte. An 8-bit grayscale
  // PNG (R = G = v) decodes to v·257/65535 = v/255 — exactly the old
  // luminance value — so existing single-channel heightmaps are unchanged,
  // while packed ones get a 200 m / 65535 ≈ 3 mm quantum instead of the
  // 0.78 m stair-terraces an 8-bit map shows on gentle slopes.
  for (let i = 0; i < data.length; i++) {
    const offset = i * 4;
    data[i] = (pixels[offset]! * 256 + pixels[offset + 1]!) / 65535;
  }

  return { width: canvas.width, height: canvas.height, data };
}

/** Bilinear amplitude in [0,1] at normalized uv; 0 for a flat sampler. */
function sampleNormalized(
  sampler: HeightSampler,
  u: number,
  v: number
): number {
  const { data, width, height } = sampler;
  if (!data || width < 2 || height < 2) return 0;

  const cu = u < 0 ? 0 : u > 1 ? 1 : u;
  const cv = v < 0 ? 0 : v > 1 ? 1 : v;
  const px = cu * (width - 1);
  const py = cv * (height - 1);
  const x0 = Math.floor(px);
  const y0 = Math.floor(py);
  const x1 = Math.min(x0 + 1, width - 1);
  const y1 = Math.min(y0 + 1, height - 1);
  const fx = px - x0;
  const fy = py - y0;

  const h00 = data[y0 * width + x0];
  const h10 = data[y0 * width + x1];
  const h01 = data[y1 * width + x0];
  const h11 = data[y1 * width + x1];

  const bilinear =
    h00 * (1 - fx) * (1 - fy) +
    h10 * fx * (1 - fy) +
    h01 * (1 - fx) * fy +
    h11 * fx * fy;

  const smoothing = sampler.smoothing ?? 1;
  if (smoothing <= 0) return bilinear;

  const smooth = sampleCatmullRom(data, width, height, x0, y0, fx, fy);
  return smoothing >= 1 ? smooth : bilinear + (smooth - bilinear) * smoothing;
}

/** Catmull-Rom weight blend of four consecutive samples at `t` in [0,1). */
function cubic(
  p0: number,
  p1: number,
  p2: number,
  p3: number,
  t: number
): number {
  const a = 2 * p1;
  const b = p2 - p0;
  const c = 2 * p0 - 5 * p1 + 4 * p2 - p3;
  const d = -p0 + 3 * p1 - 3 * p2 + p3;
  return 0.5 * (a + b * t + c * t * t + d * t * t * t);
}

/**
 * Bicubic (Catmull-Rom) height at a fractional lattice position. The 4x4
 * neighbourhood is clamped at the borders, so edge texels degrade to a
 * one-sided fit instead of wrapping onto the far side of the map.
 */
function sampleCatmullRom(
  data: Float32Array,
  width: number,
  height: number,
  x0: number,
  y0: number,
  fx: number,
  fy: number
): number {
  const cx = (i: number) => (i < 0 ? 0 : i > width - 1 ? width - 1 : i);
  const cy = (j: number) => (j < 0 ? 0 : j > height - 1 ? height - 1 : j);
  const xm = cx(x0 - 1);
  const x1 = cx(x0 + 1);
  const x2 = cx(x0 + 2);
  const rows: number[] = [];
  for (let k = -1; k <= 2; k++) {
    const row = cy(y0 + k) * width;
    rows.push(
      monotone(
        data[row + xm]!,
        data[row + x0]!,
        data[row + x1]!,
        data[row + x2]!,
        fx
      )
    );
  }
  return monotone(rows[0]!, rows[1]!, rows[2]!, rows[3]!, fy);
}

/**
 * Catmull-Rom clamped to the span of the two central samples.
 *
 * The unclamped fit overshoots at a step, and terrain steps are not rare —
 * every road cut, pad edge and lake carve stamps one deliberately. A 5 m wall
 * rings into a ~10 cm lip along its whole length, and the carve tests catch it
 * to the millimetre. Inside smooth terrain the cubic already lands between p1
 * and p2, so the clamp costs nothing there and only bites where the data is a
 * cliff — which is exactly where flat is the honest answer.
 */
function monotone(
  p0: number,
  p1: number,
  p2: number,
  p3: number,
  t: number
): number {
  const v = cubic(p0, p1, p2, p3, t);
  const lo = p1 < p2 ? p1 : p2;
  const hi = p1 < p2 ? p2 : p1;
  return v < lo ? lo : v > hi ? hi : v;
}

/** World-space elevation at a field-local (x, z) position. */
export function sampleHeightAt(
  sampler: HeightSampler,
  localX: number,
  localZ: number
): number {
  const half = sampler.worldSize / 2;
  const u = (localX + half) / sampler.worldSize;
  const v = (localZ + half) / sampler.worldSize;
  if (u < 0 || u > 1 || v < 0 || v > 1) return 0;
  return sampleNormalized(sampler, u, v) * sampler.maxHeight;
}

function surfaceHeightAt(
  sampler: HeightSampler,
  localX: number,
  localZ: number,
  baseResolution: number
): number {
  const res = Math.floor(baseResolution);
  if (res < 1 || !sampler.data) {
    return sampleHeightAt(sampler, localX, localZ);
  }

  const half = sampler.worldSize / 2;
  const step = sampler.worldSize / res;
  const gx = (localX + half) / step;
  const gz = (localZ + half) / step;
  const x0 = Math.floor(gx);
  const z0 = Math.floor(gz);
  const fx = gx - x0;
  const fz = gz - z0;

  const lx0 = x0 * step - half;
  const lz0 = z0 * step - half;
  const lx1 = lx0 + step;
  const lz1 = lz0 + step;

  const hA = sampleHeightAt(sampler, lx0, lz0);
  const hB = sampleHeightAt(sampler, lx1, lz0);
  const hC = sampleHeightAt(sampler, lx0, lz1);
  const hD = sampleHeightAt(sampler, lx1, lz1);

  if (fx + fz <= 1) {
    return hA + fx * (hB - hA) + fz * (hC - hA);
  }
  return hD + (1 - fx) * (hC - hD) + (1 - fz) * (hB - hD);
}

const TERRAIN_FOOTPRINT_RADIUS = 0.3;

/**
 * Terrain height at a world position, multi-sampled across a small footprint
 * (centre + `samples` cardinal offsets at ±`radius`) and reduced to the highest
 * finite probe so placed objects rest flush with the rendered LOD surface. Each
 * probe samples the rendered mesh lattice, falling back to the analytic height
 * when the field is flat or undecoded; with no ready field the result is 0
 * (matching {@link getTerrainHeightAt}). Defaults reproduce the cross footprint
 * (4 offsets at 0.3 m).
 */
const FOOTPRINT_OFFSET_X = [1, -1, 0, 0] as const;
const FOOTPRINT_OFFSET_Z = [0, 0, 1, -1] as const;

/** One lattice probe of `data` at a world (px, pz). Hot path — no allocation. */
function probeFieldHeight(
  data: TerrainEntityData,
  entity: number,
  px: number,
  pz: number
): number {
  const localX = px - data.worldOffset.x;
  const localZ = pz - data.worldOffset.z;
  return surfaceHeightAt(
    data.sampler,
    localX,
    localZ,
    meshSurfaceResolutionForPoint(
      Terrain.resolution[entity],
      Terrain.levels[entity],
      data.density,
      localX,
      localZ
    )
  );
}

export function sampleTerrainHeight(
  state: State,
  x: number,
  z: number,
  samples = 4,
  radius = TERRAIN_FOOTPRINT_RADIUS
): number {
  const context = getTerrainContext(state);
  let fieldData: TerrainEntityData | null = null;
  let fieldEntity = 0;
  for (const [entity, data] of context) {
    if (!data.initialized) continue;
    fieldData = data;
    fieldEntity = entity;
    break;
  }
  if (!fieldData) return 0;

  let best = probeFieldHeight(fieldData, fieldEntity, x, z);
  if (!Number.isFinite(best)) best = 0;

  const count = Math.max(0, Math.min(samples, 4));
  for (let i = 0; i < count; i++) {
    const h = probeFieldHeight(
      fieldData,
      fieldEntity,
      x + FOOTPRINT_OFFSET_X[i]! * radius,
      z + FOOTPRINT_OFFSET_Z[i]! * radius
    );
    if (Number.isFinite(h) && h > best) best = h;
  }
  return best;
}

/**
 * Ground elevation at a world (x, z) for gameplay placement and spawning.
 * Uses the rendered LOD mesh lattice with a small cardinal footprint (max
 * probe), matching what players see. Prefer this over
 * {@link getTerrainHeightAt} when aligning entities to the visible surface;
 * use {@link getTerrainHeightAt} only when you need the analytic heightmap
 * sample at a single point.
 */
export function getGroundHeight(state: State, x: number, z: number): number {
  return sampleTerrainHeight(state, x, z);
}
