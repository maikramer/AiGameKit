import * as THREE from 'three';
import { sampleHeightAt, type HeightSampler } from './height-sampler';

/**
 * Build a chunk surface as a grid of `resolution` quads spanning `size`,
 * centered on the field-local (originX, originZ). Vertex Y is displaced by the
 * sampler, so a flat sampler yields a flat plane and a heightmap-backed sampler
 * yields terrain — the same code path across phases.
 *
 * Normals are computed analytically from the sampler with a fixed world-space
 * epsilon (not `computeVertexNormals`), so a vertex shared by two chunks of
 * different LOD gets the *same* normal on both sides — no lighting seam. A
 * vertical skirt of `skirtDepth` plugs the geometric T-junction gaps.
 */
export function buildChunkGeometry(
  sampler: HeightSampler,
  originX: number,
  originZ: number,
  size: number,
  resolution: number,
  skirtDepth = 0,
  normalEpsilon = 1
): THREE.BufferGeometry {
  const segments = Math.max(1, resolution);
  const verts = segments + 1;
  const half = size / 2;
  const step = size / segments;
  const e = normalEpsilon;

  const gridCount = verts * verts;
  const hasSkirt = skirtDepth > 0;
  const skirtCount = hasSkirt ? verts * 4 : 0;
  const total = gridCount + skirtCount;
  const positions = new Float32Array(total * 3);
  const normals = new Float32Array(total * 3);
  const uvs = new Float32Array(total * 2);
  const indices: number[] = [];

  for (let z = 0; z < verts; z++) {
    for (let x = 0; x < verts; x++) {
      const i = z * verts + x;
      const localX = originX - half + x * step;
      const localZ = originZ - half + z * step;

      positions[i * 3] = localX - originX;
      positions[i * 3 + 1] = sampleHeightAt(sampler, localX, localZ);
      positions[i * 3 + 2] = localZ - originZ;

      // Central-difference normal from the height field (fixed epsilon → shared
      // across chunk boundaries regardless of LOD).
      const hL = sampleHeightAt(sampler, localX - e, localZ);
      const hR = sampleHeightAt(sampler, localX + e, localZ);
      const hD = sampleHeightAt(sampler, localX, localZ - e);
      const hU = sampleHeightAt(sampler, localX, localZ + e);
      let nx = hL - hR;
      let ny = 2 * e;
      let nz = hD - hU;
      const inv = 1 / Math.hypot(nx, ny, nz);
      nx *= inv;
      ny *= inv;
      nz *= inv;
      normals[i * 3] = nx;
      normals[i * 3 + 1] = ny;
      normals[i * 3 + 2] = nz;

      uvs[i * 2] = x / segments;
      uvs[i * 2 + 1] = z / segments;
    }
  }

  for (let z = 0; z < segments; z++) {
    for (let x = 0; x < segments; x++) {
      const a = z * verts + x;
      const b = a + 1;
      const c = a + verts;
      const d = c + 1;
      indices.push(a, c, b, b, c, d);
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
        positions[s * 3] = positions[g * 3];
        positions[s * 3 + 1] = positions[g * 3 + 1] - skirtDepth;
        positions[s * 3 + 2] = positions[g * 3 + 2];
        normals[s * 3] = normals[g * 3];
        normals[s * 3 + 1] = normals[g * 3 + 1];
        normals[s * 3 + 2] = normals[g * 3 + 2];
        uvs[s * 2] = uvs[g * 2];
        uvs[s * 2 + 1] = uvs[g * 2 + 1];
      }
      for (let k = 0; k < segments; k++) {
        const g0 = gridIndexAt(k);
        const g1 = gridIndexAt(k + 1);
        const s0 = base + k;
        const s1 = base + k + 1;
        indices.push(g0, s0, g1, g1, s0, s1);
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
  geometry.setIndex(indices);
  geometry.computeBoundingSphere();

  return geometry;
}
