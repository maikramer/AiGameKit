import * as THREE from 'three';

/**
 * Build a river surface as a ribbon along the polyline. At each path node we
 * emit THREE vertices: left bank, axis (centre), right bank — offset along the
 * miter normal (average of the incoming/outgoing segment normals), so curves
 * don't gap. The axis vertex is essential: it carries `aWaterT = 0` so the
 * depth/alpha falloff (which reads the `vWaterT` varying) actually interpolates
 * from deep centre (t=0) to bank (t=1). A two-vertex ribbon (banks only) would
 * leave `vWaterT ≈ 1` everywhere and render fully transparent.
 *
 * UV.u is the accumulated length along the path (for flow), UV.v is 0..1 across
 * the channel (left=0, axis=0.5, right=1). `aWaterT` (0 at the axis, 1 at the
 * banks) is baked here so the water material can be shape-agnostic.
 *
 * Vertices lie in the XZ plane by default; when `surfaceHeights` is provided
 * (one water-surface Y per path node, from the carve), each node's three
 * vertices are lifted to that Y so the ribbon follows the river's descent
 * (terrain-following water). The caller then places the mesh at (0,0,0) since
 * the vertices already carry world X/Z and the surface Y.
 *
 * @param path            Flat polyline `[x0,z0,x1,z1,...]` in field-local world coords.
 * @param width           Channel width (m). The ribbon is oversized by `pad` so the
 *                        alpha fade at the bank falls over the carved channel.
 * @param surfaceHeights  Water-surface Y per path node (field-local). Empty/omitted
 *                        → flat ribbon at y=0 (legacy/test path).
 */
export function makeRiverGeometry(
  path: number[],
  width: number,
  surfaceHeights: number[] = []
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

    // Water-surface Y for this node (terrain-following), or 0 if not supplied.
    const sy = i < surfaceHeights.length ? surfaceHeights[i]! : 0;
    // Three vertices per node: left bank, axis, right bank. All lifted to the
    // node's surface Y so the ribbon descends with the river.
    positions.push(x - ox, sy, z - oz); // left bank
    positions.push(x, sy, z); // axis (centre)
    positions.push(x + ox, sy, z + oz); // right bank
    uvs.push(accLen, 0, accLen, 0.5, accLen, 1);
    // aWaterT: 1 at banks, 0 at axis — drives depth/alpha interpolation.
    waterT.push(1, 0, 1);

    // Accumulate length at the node boundary (for the next node's u).
    if (i < nodeCount - 1) {
      const qx = path[(i + 1) * 2]!;
      const qz = path[(i + 1) * 2 + 1]!;
      accLen += Math.hypot(qx - x, qz - z);
    }
  }

  // Two quads (4 triangles) between consecutive nodes: left-bank→axis and
  // axis→right-bank. Winding is CCW when viewed from +Y (above) so the face
  // normal points up and the surface isn't backface-culled — matching
  // makeLakeGeometry's `(centre, next, current)` winding.
  for (let i = 0; i < nodeCount - 1; i++) {
    const l0 = i * 3; // node i, left bank
    const a0 = i * 3 + 1; // node i, axis
    const r0 = i * 3 + 2; // node i, right bank
    const l1 = (i + 1) * 3; // node i+1, left bank
    const a1 = (i + 1) * 3 + 1; // node i+1, axis
    const r1 = (i + 1) * 3 + 2; // node i+1, right bank
    // Left half-quad (l0, a0, l1, a1): two CCW-from-above triangles.
    indices.push(l0, a0, l1, l1, a0, a1);
    // Right half-quad (a0, r0, a1, r1): two CCW-from-above triangles.
    indices.push(a0, r0, a1, a1, r0, r1);
  }

  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geo.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2));
  geo.setAttribute('aWaterT', new THREE.Float32BufferAttribute(waterT, 1));
  geo.setIndex(indices);
  geo.computeVertexNormals();
  return geo;
}
