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

export async function loadHeightmapFromUrl(
  url: string
): Promise<HeightSamplerData> {
  const response = await fetch(url);
  const blob = await response.blob();
  const bitmap = await createImageBitmap(blob);

  const canvas =
    typeof OffscreenCanvas !== "undefined"
      ? new OffscreenCanvas(bitmap.width, bitmap.height)
      : (() => {
          const c = document.createElement("canvas");
          c.width = bitmap.width;
          c.height = bitmap.height;
          return c;
        })();

  const ctx = canvas.getContext("2d")!;
  ctx.drawImage(bitmap, 0, 0);
  bitmap.close();

  const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
  const pixels = imageData.data;
  const data = new Float32Array(canvas.width * canvas.height);

  for (let i = 0; i < data.length; i++) {
    const offset = i * 4;
    data[i] =
      (pixels[offset]! * 0.299 +
        pixels[offset + 1]! * 0.587 +
        pixels[offset + 2]! * 0.114) /
      255;
  }

  return { width: canvas.width, height: canvas.height, data };
}

/** Bilinear amplitude in [0,1] at normalized uv; 0 for a flat sampler. */
function sampleNormalized(sampler: HeightSampler, u: number, v: number): number {
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

  return (
    h00 * (1 - fx) * (1 - fy) +
    h10 * fx * (1 - fy) +
    h01 * (1 - fx) * fy +
    h11 * fx * fy
  );
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
