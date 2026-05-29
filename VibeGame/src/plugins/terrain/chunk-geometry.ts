import * as THREE from 'three';
import { sampleHeightAt, type HeightSampler } from './height-sampler';

/**
 * Build a chunk surface as a grid of `resolution` quads spanning `size`,
 * centered on the field-local (originX, originZ). Vertex Y is displaced by the
 * sampler, so a flat sampler yields a flat plane and a heightmap-backed sampler
 * yields terrain — the same code path across phases.
 */
export function buildChunkGeometry(
  sampler: HeightSampler,
  originX: number,
  originZ: number,
  size: number,
  resolution: number
): THREE.BufferGeometry {
  const segments = Math.max(1, resolution);
  const verts = segments + 1;
  const half = size / 2;
  const step = size / segments;

  const positions = new Float32Array(verts * verts * 3);
  const uvs = new Float32Array(verts * verts * 2);
  const indices: number[] = [];

  for (let z = 0; z < verts; z++) {
    for (let x = 0; x < verts; x++) {
      const i = z * verts + x;
      const localX = originX - half + x * step;
      const localZ = originZ - half + z * step;

      positions[i * 3] = localX - originX;
      positions[i * 3 + 1] = sampleHeightAt(sampler, localX, localZ);
      positions[i * 3 + 2] = localZ - originZ;

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

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  geometry.setAttribute('uv', new THREE.BufferAttribute(uvs, 2));
  geometry.setIndex(indices);
  geometry.computeVertexNormals();
  geometry.computeBoundingSphere();

  return geometry;
}
