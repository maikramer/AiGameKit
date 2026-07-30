import * as THREE from 'three';
import { sampleHeightAt, type HeightSampler } from './height-sampler';

/**
 * Write a unit normal from central differences (dh/dx, 1, dh/dz).
 */
function writeNormal(
  normals: Float32Array,
  i: number,
  hL: number,
  hR: number,
  hD: number,
  hU: number,
  spanX: number,
  spanZ: number
): void {
  let nx = (hL - hR) / spanX;
  let ny = 1;
  let nz = (hD - hU) / spanZ;
  const inv = 1 / Math.hypot(nx, ny, nz);
  normals[i * 3] = nx * inv;
  normals[i * 3 + 1] = ny * inv;
  normals[i * 3 + 2] = nz * inv;
}

/**
 * Build a chunk surface as a grid of `resolution` quads spanning `size`,
 * centered on the field-local (originX, originZ).
 *
 * Seam lighting: frontier verts on shared chunk edges must be identical in
 * direction and sense on both neighbours. Per-chunk finite differences only
 * see that chunk's verts and crease shading. Border verts therefore sample the
 * shared heightfield with a field-constant world-space epsilon. No height
 * morph, overlap push, or relief seal — those read as edge fades and fight
 * the normal equalisation.
 *
 * UVs are world-space when `textureTileSize > 0`: `uv = fieldLocalXZ / tile`.
 * A vertical skirt of `skirtDepth` plugs residual geometric T-junction gaps.
 */
export function buildChunkGeometry(
  sampler: HeightSampler,
  originX: number,
  originZ: number,
  size: number,
  resolution: number,
  skirtDepth = 0,
  normalEpsilon = 1,
  textureTileSize = 0
): THREE.BufferGeometry {
  const segments = Math.max(1, resolution);
  const verts = segments + 1;
  const half = size / 2;
  const step = size / segments;
  const e = normalEpsilon;

  const gridCount = verts * verts;
  const hasSkirt = skirtDepth > 0;
  const total = gridCount + (hasSkirt ? verts * 4 : 0);
  const positions = new Float32Array(total * 3);
  const normals = new Float32Array(total * 3);
  const uvs = new Float32Array(total * 2);
  const surfaceIndexCount = segments * segments * 6;
  const skirtIndexCount = hasSkirt ? 4 * segments * 6 : 0;
  const indices = new Uint32Array(surfaceIndexCount + skirtIndexCount);
  let indexWrite = 0;

  for (let z = 0; z < verts; z++) {
    for (let x = 0; x < verts; x++) {
      const i = z * verts + x;
      const localX = originX - half + x * step;
      const localZ = originZ - half + z * step;

      positions[i * 3] = localX - originX;
      positions[i * 3 + 1] = sampleHeightAt(sampler, localX, localZ);
      positions[i * 3 + 2] = localZ - originZ;

      if (textureTileSize > 0) {
        uvs[i * 2] = localX / textureTileSize;
        uvs[i * 2 + 1] = localZ / textureTileSize;
      } else {
        uvs[i * 2] = x / segments;
        uvs[i * 2 + 1] = z / segments;
      }
    }
  }

  // Frontier normals from the shared heightfield (same ε → identical on both
  // neighbours). Interior uses already-sampled grid neighbours.
  for (let z = 0; z < verts; z++) {
    for (let x = 0; x < verts; x++) {
      const i = z * verts + x;
      const localX = originX - half + x * step;
      const localZ = originZ - half + z * step;
      const onBorder = x === 0 || x === segments || z === 0 || z === segments;

      if (onBorder) {
        writeNormal(
          normals,
          i,
          sampleHeightAt(sampler, localX - e, localZ),
          sampleHeightAt(sampler, localX + e, localZ),
          sampleHeightAt(sampler, localX, localZ - e),
          sampleHeightAt(sampler, localX, localZ + e),
          2 * e,
          2 * e
        );
      } else {
        writeNormal(
          normals,
          i,
          positions[(z * verts + (x - 1)) * 3 + 1]!,
          positions[(z * verts + (x + 1)) * 3 + 1]!,
          positions[((z - 1) * verts + x) * 3 + 1]!,
          positions[((z + 1) * verts + x) * 3 + 1]!,
          2 * step,
          2 * step
        );
      }
    }
  }

  for (let z = 0; z < segments; z++) {
    for (let x = 0; x < segments; x++) {
      const a = z * verts + x;
      const b = a + 1;
      const c = a + verts;
      const d = c + 1;
      indices[indexWrite++] = a;
      indices[indexWrite++] = c;
      indices[indexWrite++] = b;
      indices[indexWrite++] = b;
      indices[indexWrite++] = c;
      indices[indexWrite++] = d;
    }
  }

  if (hasSkirt) {
    const addSkirtStrip = (
      gridIndexAt: (k: number) => number,
      base: number
    ): void => {
      for (let k = 0; k < verts; k++) {
        const g = gridIndexAt(k);
        const s = base + k;
        positions[s * 3] = positions[g * 3]!;
        positions[s * 3 + 1] = positions[g * 3 + 1]! - skirtDepth;
        positions[s * 3 + 2] = positions[g * 3 + 2]!;
        normals[s * 3] = normals[g * 3]!;
        normals[s * 3 + 1] = normals[g * 3 + 1]!;
        normals[s * 3 + 2] = normals[g * 3 + 2]!;
        uvs[s * 2] = uvs[g * 2]!;
        uvs[s * 2 + 1] = uvs[g * 2 + 1]!;
      }
      for (let k = 0; k < segments; k++) {
        const g0 = gridIndexAt(k);
        const g1 = gridIndexAt(k + 1);
        const s0 = base + k;
        const s1 = base + k + 1;
        indices[indexWrite++] = g0;
        indices[indexWrite++] = s0;
        indices[indexWrite++] = g1;
        indices[indexWrite++] = g1;
        indices[indexWrite++] = s0;
        indices[indexWrite++] = s1;
      }
    };

    const top = gridCount;
    const bottom = gridCount + verts;
    const left = gridCount + verts * 2;
    const right = gridCount + verts * 3;
    addSkirtStrip((x) => x, top);
    addSkirtStrip((x) => segments * verts + x, bottom);
    addSkirtStrip((z) => z * verts, left);
    addSkirtStrip((z) => z * verts + segments, right);
  }

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  geometry.setAttribute('normal', new THREE.BufferAttribute(normals, 3));
  geometry.setAttribute('uv', new THREE.BufferAttribute(uvs, 2));
  geometry.setIndex(new THREE.BufferAttribute(indices, 1));
  geometry.computeBoundingSphere();

  return geometry;
}
