import * as THREE from 'three';

/**
 * Build a river surface as a ribbon along the polyline. At each path node we
 * emit two vertices offset ±halfWidth·pad along the miter normal (average of
 * the incoming/outgoing segment normals), so curves don't gap. UV.u is the
 * accumulated length along the path (for flow), UV.v is 0..1 across the
 * channel. `aWaterT` (0 at the axis, 1 at the banks) is baked here so the
 * water material can be shape-agnostic.
 *
 * Vertices lie in the XZ plane (+Y up); the caller places the mesh at the
 * water surface height with no rotation, matching `makeLakeGeometry`.
 *
 * @param path  Flat polyline `[x0,z0,x1,z1,...]` in field-local world coords.
 * @param width Channel width (m). The ribbon is oversized by `pad` so the
 *              alpha fade at the bank falls over the carved channel, not a
 *              hard polygon edge (analogous to makeLakeGeometry's pad).
 */
export function makeRiverGeometry(
  path: number[],
  width: number
): THREE.BufferGeometry {
  if (path.length < 4) {
    throw new Error(
      'makeRiverGeometry: path must have at least 2 points (4 numbers)'
    );
  }
  const nodeCount = path.length / 2;
  const pad = 1.05;
  const halfWidth = (width / 2) * pad;

  const positions: number[] = [];
  const uvs: number[] = [];
  const waterT: number[] = [];
  const indices: number[] = [];

  let accLen = 0;

  for (let i = 0; i < nodeCount; i++) {
    const x = path[i * 2]!;
    const z = path[i * 2 + 1]!;

    // Miter normal: average of incoming and outgoing segment normals.
    let nx = 0;
    let nz = 0;
    if (i > 0) {
      const px = path[(i - 1) * 2]!;
      const pz = path[(i - 1) * 2 + 1]!;
      const dx = x - px;
      const dz = z - pz;
      const len = Math.hypot(dx, dz) || 1;
      nx += -dz / len;
      nz += dx / len;
    }
    if (i < nodeCount - 1) {
      const qx = path[(i + 1) * 2]!;
      const qz = path[(i + 1) * 2 + 1]!;
      const dx = qx - x;
      const dz = qz - z;
      const len = Math.hypot(dx, dz) || 1;
      nx += -dz / len;
      nz += dx / len;
    }
    const nlen = Math.hypot(nx, nz) || 1;
    nx /= nlen;
    nz /= nlen;
    // Clamp the miter length so near-180° turns don't spike (bevel fallback).
    const miterClamp = 1.5;
    const miterLen = Math.min(miterClamp, 1);
    const ox = nx * halfWidth * miterLen;
    const oz = nz * halfWidth * miterLen;

    // Two bank vertices for this node.
    positions.push(x - ox, 0, z - oz); // left bank (v=0)
    positions.push(x + ox, 0, z + oz); // right bank (v=1)
    uvs.push(accLen, 0, accLen, 1);
    waterT.push(1, 1); // both banks at full |t|; the axis is interpolated by the GPU

    // Accumulate length at the node boundary (for the next node's u).
    if (i < nodeCount - 1) {
      const qx = path[(i + 1) * 2]!;
      const qz = path[(i + 1) * 2 + 1]!;
      accLen += Math.hypot(qx - x, qz - z);
    }
  }

  // Ribbon quads between consecutive nodes.
  for (let i = 0; i < nodeCount - 1; i++) {
    const a = i * 2; // node i, left
    const b = i * 2 + 1; // node i, right
    const c = (i + 1) * 2; // node i+1, left
    const d = (i + 1) * 2 + 1; // node i+1, right
    // Two triangles per quad; winding keeps the face normal at +Y.
    indices.push(a, c, b, b, c, d);
  }

  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geo.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2));
  geo.setAttribute('aWaterT', new THREE.Float32BufferAttribute(waterT, 1));
  geo.setIndex(indices);
  geo.computeVertexNormals();
  return geo;
}
