import * as THREE from 'three';
import { pointInPolygon } from './adapters';
import type { BiomeRegionInfo } from './parser';

/** Splat resolution (texels per side). ~4 m/texel over a 2 km field. */
const SPLAT_SIZE = 512;

/** Soft transition half-width as a fraction of the splat size (box-blur radius). */
const BLEND_FRACTION = 0.012;

export interface BiomeSplat {
  texture: THREE.DataTexture;
  /** Layer texture URLs in channel order (R,G,B,A); max 4. */
  layerUrls: string[];
}

/** Separable box blur over one float channel buffer (in place via scratch). */
function boxBlur(src: Float32Array, n: number, radius: number): Float32Array {
  if (radius < 1) return src;
  const tmp = new Float32Array(n * n);
  const out = new Float32Array(n * n);
  const norm = 1 / (radius * 2 + 1);
  // Horizontal pass.
  for (let y = 0; y < n; y++) {
    const row = y * n;
    for (let x = 0; x < n; x++) {
      let sum = 0;
      for (let k = -radius; k <= radius; k++) {
        const xi = Math.min(n - 1, Math.max(0, x + k));
        sum += src[row + xi];
      }
      tmp[row + x] = sum * norm;
    }
  }
  // Vertical pass.
  for (let x = 0; x < n; x++) {
    for (let y = 0; y < n; y++) {
      let sum = 0;
      for (let k = -radius; k <= radius; k++) {
        const yi = Math.min(n - 1, Math.max(0, y + k));
        sum += tmp[yi * n + x];
      }
      out[y * n + x] = sum * norm;
    }
  }
  return out;
}

/**
 * Bake an RGBA splat texture from the biome regions: one biome layer per
 * channel (max 4). Each region's polygon is rasterised into its channel, then
 * box-blurred so biome borders cross-fade instead of switching hard. Only
 * regions with a `terrainTexture` are included. Returns null when there is
 * nothing to blend.
 */
export function buildBiomeSplat(
  regions: BiomeRegionInfo[],
  worldMinX: number,
  worldMinZ: number,
  worldSizeX: number,
  worldSizeZ: number
): BiomeSplat | null {
  const layered = regions.filter(
    (r) => r.terrainTexture && r.vertices.length >= 3
  );
  if (layered.length === 0) return null;
  const layers = layered.slice(0, 4);
  const n = SPLAT_SIZE;

  // Rasterise hard masks per channel.
  const masks: Float32Array[] = layers.map(() => new Float32Array(n * n));
  for (let j = 0; j < n; j++) {
    const wz = worldMinZ + ((j + 0.5) / n) * worldSizeZ;
    for (let i = 0; i < n; i++) {
      const wx = worldMinX + ((i + 0.5) / n) * worldSizeX;
      for (let l = 0; l < layers.length; l++) {
        if (pointInPolygon(wx, wz, layers[l]!.vertices)) {
          masks[l]![j * n + i] = 1;
          break; // wedges are disjoint; first match wins
        }
      }
    }
  }

  const radius = Math.max(1, Math.round(n * BLEND_FRACTION));
  const blurred = masks.map((m) => boxBlur(m, n, radius));

  const data = new Uint8Array(n * n * 4);
  for (let p = 0; p < n * n; p++) {
    for (let c = 0; c < 4; c++) {
      const v = c < blurred.length ? blurred[c]![p] : 0;
      data[p * 4 + c] = Math.round(Math.min(1, Math.max(0, v)) * 255);
    }
  }

  const texture = new THREE.DataTexture(data, n, n, THREE.RGBAFormat);
  texture.wrapS = THREE.ClampToEdgeWrapping;
  texture.wrapT = THREE.ClampToEdgeWrapping;
  texture.minFilter = THREE.LinearFilter;
  texture.magFilter = THREE.LinearFilter;
  texture.needsUpdate = true;

  return { texture, layerUrls: layers.map((r) => r.terrainTexture!) };
}
