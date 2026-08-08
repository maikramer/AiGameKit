import * as THREE from 'three';
import type { TrackData } from './data';

export interface TrackMeshes {
  group: THREE.Group;
  road: THREE.Mesh;
  centerline: THREE.Line | null;
  startLine: THREE.Mesh | null;
  walls: THREE.Group;
  dispose: () => void;
}

// NFS Underground / Carbon night-track palette.
const ROAD_COLOR = 0x2a2a35;       // dark asphalt
const ROAD_LINE_COLOR = 0x00ddff;  // neon cyan centerline (NFS style)
const KERB_RED = 0xff2244;         // vibrant neon red
const KERB_WHITE = 0xffffff;       // pure white
const WALL_COLOR = 0x444455;       // dark concrete barriers
const ROAD_EDGE_GLOW = 0x00aaff;   // subtle blue edge glow

// ---- Helper functions (defined before use) ---------------------------------

/**
 * Subdivide a closed polyline with Catmull-Rom interpolation so tight hairpins
 * become smooth arcs. Without this, a wide ribbon (halfWidth≈7) self-intersects
 * on sharp corners ("estrada se esbarrando") because the ribbon follows the raw
 * polyline with hard angle breaks.
 *
 * @param pts      Input control points (XZ polyline).
 * @param closed   Loop back to the first point.
 * @param steps    Interpolation steps per segment.
 */
function subdivideCenterline(
  pts: THREE.Vector3[],
  closed: boolean,
  steps: number
): THREE.Vector3[] {
  if (pts.length < 3 || steps <= 1) return pts.map((p) => p.clone());
  const out: THREE.Vector3[] = [];
  const n = pts.length;
  const at = (i: number): THREE.Vector3 => pts[((i % n) + n) % n]!;
  const count = closed ? n : n - 1;
  for (let i = 0; i < count; i++) {
    const p0 = at(i - 1);
    const p1 = at(i);
    const p2 = at(i + 1);
    const p3 = at(i + 2);
    for (let s = 0; s < steps; s++) {
      const t = s / steps;
      // Catmull-Rom basis (uniform) — interpolates X, Y (elevation) and Z.
      const t2 = t * t;
      const t3 = t2 * t;
      const x =
        0.5 *
        (2 * p1.x +
          (-p0.x + p2.x) * t +
          (2 * p0.x - 5 * p1.x + 4 * p2.x - p3.x) * t2 +
          (-p0.x + 3 * p1.x - 3 * p2.x + p3.x) * t3);
      const y =
        0.5 *
        (2 * p1.y +
          (-p0.y + p2.y) * t +
          (2 * p0.y - 5 * p1.y + 4 * p2.y - p3.y) * t2 +
          (-p0.y + 3 * p1.y - 3 * p2.y + p3.y) * t3);
      const z =
        0.5 *
        (2 * p1.z +
          (-p0.z + p2.z) * t +
          (2 * p0.z - 5 * p1.z + 4 * p2.z - p3.z) * t2 +
          (-p0.z + 3 * p1.z - 3 * p2.z + p3.z) * t3);
      out.push(new THREE.Vector3(x, y, z));
    }
  }
  if (!closed && pts.length > 0) out.push(pts[pts.length - 1]!.clone());
  return out;
}

function buildRibbon(
  pts: THREE.Vector3[],
  halfWidth: number,
  _closed: boolean
): THREE.Mesh {
  if (pts.length < 2) return new THREE.Mesh();
  const positions: number[] = [];
  const uvs: number[] = [];
  const indices: number[] = [];
  let cum = 0;
  for (let i = 0; i < pts.length; i++) {
    const p = pts[i]!;
    // Tangent (with end handling).
    const prev = pts[Math.max(0, i - 1)]!;
    const next = pts[Math.min(pts.length - 1, i + 1)]!;
    const tx = next.x - prev.x;
    const tz = next.z - prev.z;
    const tl = Math.hypot(tx, tz) || 1;
    // Normal = perpendicular to tangent (in XZ).
    const nx = -tz / tl;
    const nz = tx / tl;
    if (i > 0) cum += Math.hypot(p.x - prev.x, p.z - prev.z);
    // Left and right edge vertices. Road surface rides on the point's
    // elevation (p.y) + a small skin offset to avoid z-fighting with ground.
    positions.push(p.x + nx * halfWidth, p.y + 0.02, p.z + nz * halfWidth);
    positions.push(p.x - nx * halfWidth, p.y + 0.02, p.z - nz * halfWidth);
    uvs.push(0, cum / 8, 1, cum / 8);
  }
  for (let i = 0; i < pts.length - 1; i++) {
    const a = i * 2;
    indices.push(a, a + 1, a + 2, a + 1, a + 3, a + 2);
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geo.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2));
  geo.setIndex(indices);
  geo.computeVertexNormals();
  const mat = new THREE.MeshStandardMaterial({
    color: ROAD_COLOR,
    roughness: 0.45,   // semi-gloss (wet asphalt look)
    metalness: 0.15,  // slight reflection
  });
  const mesh = new THREE.Mesh(geo, mat);
  mesh.receiveShadow = true;
  return mesh;
}

function buildKerb(
  pts: THREE.Vector3[],
  halfWidth: number,
  side: number,
  closed: boolean
): THREE.Mesh {
  // A thin striped ribbon just outside the road edge. We fake the stripe with
  // vertex colors alternating red/white per segment.
  const width = 0.6;
  const positions: number[] = [];
  const colors: number[] = [];
  const indices: number[] = [];
  const count = closed ? pts.length : pts.length - 1;
  for (let i = 0; i < pts.length; i++) {
    const p = pts[i]!;
    const prev = pts[Math.max(0, i - 1)]!;
    const next = pts[Math.min(pts.length - 1, i + 1)]!;
    const tx = next.x - prev.x;
    const tz = next.z - prev.z;
    const tl = Math.hypot(tx, tz) || 1;
    const nx = (-tz / tl) * side;
    const nz = (tx / tl) * side;
    const inner = halfWidth;
    const outer = halfWidth + width;
    positions.push(p.x + nx * (inner / Math.abs(side || 1)), p.y + 0.06, p.z + nz * (inner / Math.abs(side || 1)));
    positions.push(p.x + nx * (outer / Math.abs(side || 1)), p.y + 0.06, p.z + nz * (outer / Math.abs(side || 1)));
    const stripe = i % 2 === 0;
    const r = stripe ? KERB_RED : KERB_WHITE;
    const g = stripe ? KERB_RED : KERB_WHITE;
    const b = stripe ? KERB_RED : KERB_WHITE;
    // Normalise red/white as 0..1 floats.
    const cr = ((r >> 16) & 0xff) / 255;
    const cg = ((g >> 8) & 0xff) / 255;
    const cb = (b & 0xff) / 255;
    colors.push(cr, cg, cb, cr, cg, cb);
  }
  void count;
  for (let i = 0; i < pts.length - 1; i++) {
    const a = i * 2;
    indices.push(a, a + 1, a + 2, a + 1, a + 3, a + 2);
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geo.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
  geo.setIndex(indices);
  geo.computeVertexNormals();
  const mat = new THREE.MeshStandardMaterial({
    vertexColors: true,
    roughness: 0.7,
    metalness: 0.0,
  });
  return new THREE.Mesh(geo, mat);
}

function buildCenterline(pts: THREE.Vector3[]): THREE.Line | null {
  if (pts.length < 2) return null;
  // Lift each point to the track elevation so the dashed line hugs hills.
  const elevated = pts.map((p) => new THREE.Vector3(p.x, p.y + 0.085, p.z));
  const geo = new THREE.BufferGeometry().setFromPoints(elevated);
  // Neon cyan glow — NFS style.
  const mat = new THREE.LineDashedMaterial({
    color: ROAD_LINE_COLOR,
    dashSize: 2.0,
    gapSize: 2.0,
    linewidth: 2, // note: linewidth > 1 is WebGL-implementation-dependent
    transparent: true,
    opacity: 0.9,
  });
  const line = new THREE.Line(geo, mat);
  line.computeLineDistances();
  return line;
}

function buildEdgeGlow(
  pts: THREE.Vector3[],
  halfWidth: number,
  side: number,
  _closed: boolean
): THREE.Mesh {
  // Thin emissive strip along the road edge — NFS neon aesthetic.
  const width = 0.08;
  const positions: number[] = [];
  const indices: number[] = [];
  for (let i = 0; i < pts.length; i++) {
    const p = pts[i]!;
    const prev = pts[Math.max(0, i - 1)]!;
    const next = pts[Math.min(pts.length - 1, i + 1)]!;
    const tx = next.x - prev.x;
    const tz = next.z - prev.z;
    const tl = Math.hypot(tx, tz) || 1;
    const nx = (-tz / tl) * side;
    const nz = (tx / tl) * side;
    positions.push(p.x + nx * halfWidth, p.y + 0.065, p.z + nz * halfWidth);
    positions.push(p.x + nx * (halfWidth + width), p.y + 0.065, p.z + nz * (halfWidth + width));
  }
  for (let i = 0; i < pts.length - 1; i++) {
    const a = i * 2;
    indices.push(a, a + 1, a + 2, a + 1, a + 3, a + 2);
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geo.setIndex(indices);
  geo.computeVertexNormals();
  const mat = new THREE.MeshStandardMaterial({
    color: ROAD_EDGE_GLOW,
    emissive: ROAD_EDGE_GLOW,
    emissiveIntensity: 1.5,
    roughness: 0.2,
    metalness: 0.4,
  });
  return new THREE.Mesh(geo, mat);
}

function buildStartLine(
  a: THREE.Vector3,
  b: THREE.Vector3,
  halfWidth: number
): THREE.Mesh {
  // A checkered-looking bar across the road at point 0.
  const tx = b.x - a.x;
  const tz = b.z - a.z;
  const tl = Math.hypot(tx, tz) || 1;
  const nx = -tz / tl;
  const nz = tx / tl;
  const geo = new THREE.PlaneGeometry(halfWidth * 2, 1.4);
  geo.rotateX(-Math.PI / 2);
  const mat = new THREE.MeshStandardMaterial({
    color: 0xffffff,
    roughness: 0.6,
  });
  const mesh = new THREE.Mesh(geo, mat);
  mesh.position.set(a.x, a.y + 0.10, a.z);
  mesh.rotation.y = Math.atan2(tx, tz);
  // Use the normal so the bar lies across the road.
  void nx;
  void nz;
  return mesh;
}

function buildWall(
  pts: THREE.Vector3[],
  offset: number,
  height: number,
  closed: boolean
): THREE.Mesh {
  // A single merged box-strip wall. Built as a TriangleStrip-like BufferGeometry
  // of vertical quads, so it follows the circuit without 100s of mesh nodes.
  const positions: number[] = [];
  const indices: number[] = [];
  for (let i = 0; i < pts.length; i++) {
    const p = pts[i]!;
    const prev = pts[Math.max(0, i - 1)]!;
    const next = pts[Math.min(pts.length - 1, i + 1)]!;
    const tx = next.x - prev.x;
    const tz = next.z - prev.z;
    const tl = Math.hypot(tx, tz) || 1;
    const nx = (-tz / tl) * Math.sign(offset);
    const nz = (tx / tl) * Math.sign(offset);
    const ox = nx * Math.abs(offset);
    const oz = nz * Math.abs(offset);
    // Wall base follows the track elevation so barriers stay planted on hills.
    const base = p.y + 0.04;
    positions.push(p.x + ox, base, p.z + oz);
    positions.push(p.x + ox, base + height, p.z + oz);
  }
  const top = closed ? pts.length : pts.length - 1;
  for (let i = 0; i < top; i++) {
    const a = i * 2;
    const b = ((i + 1) % pts.length) * 2;
    indices.push(a, a + 1, b, a + 1, b + 1, b);
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geo.setIndex(indices);
  geo.computeVertexNormals();
  const mat = new THREE.MeshStandardMaterial({
    color: WALL_COLOR,
    roughness: 0.8,
    metalness: 0.1,
  });
  const mesh = new THREE.Mesh(geo, mat);
  mesh.castShadow = false;
  mesh.receiveShadow = false;
  return mesh;
}

// ---- Main entry point -----------------------------------------------------

/**
 * Build a drivable track mesh + visual walls + start/finish line from a
 * centerline polyline. This is a self-contained ribbon builder (not the terrain
 * road plugin) because a racing circuit needs its own drivable collider and
 * barriers, not a decal painted on a heightfield. The returned meshes are static
 * (no per-frame work); collisions come from wall boxes the caller can add as
 * physics parts, and the floor itself is a large plane under everything.
 *
 * The ribbon is built along the centerline with arc-length UVs so a tiling
 * asphalt texture would map cleanly; for the arcade look we ship a flat-shaded
 * dark surface with kerb stripes on the edges and a dashed centerline.
 */
export function buildTrackMeshes(track: TrackData): TrackMeshes {
  const group = new THREE.Group();
  group.name = 'Track';

  const { centerline, halfWidth, closed } = track;
  const n = Math.floor(centerline.length / 2);
  const elevations = track.elevations ?? [];
  const hasElev = elevations.length === n && n > 0;
  const rawPts: THREE.Vector3[] = [];
  for (let i = 0; i < n; i++) {
    rawPts.push(new THREE.Vector3(centerline[i * 2]!, hasElev ? elevations[i]! : 0, centerline[i * 2 + 1]!));
  }
  // Smooth the polyline so tight hairpins don't make the wide ribbon
  // self-intersect (avoids the road "se esbarrando" on sharp corners).
  // Interpolates Y (elevation) too so hills are smooth.
  const pts = subdivideCenterline(rawPts, closed, 4);
  if (closed && pts.length > 0) pts.push(pts[0]!.clone());

  // --- Road ribbon ------------------------------------------------------
  const road = buildRibbon(pts, halfWidth, closed);
  road.name = 'RoadSurface';
  group.add(road);

  // --- Kerbs (red/white stripe along both edges) ------------------------
  const leftKerb = buildKerb(pts, halfWidth, +1, closed);
  const rightKerb = buildKerb(pts, halfWidth, -1, closed);
  group.add(leftKerb, rightKerb);

  // --- Neon Edge Glow (thin emissive strips at road edges) ---------------
  const leftGlow = buildEdgeGlow(pts, halfWidth, +1, closed);
  const rightGlow = buildEdgeGlow(pts, halfWidth, -1, closed);
  group.add(leftGlow, rightGlow);

  // --- Dashed centerline ------------------------------------------------
  const centerlineLine = buildCenterline(pts);
  if (centerlineLine) group.add(centerlineLine);

  // --- Start/Finish line ------------------------------------------------
  const startLine = buildStartLine(pts[0]!, pts[1]!, halfWidth);
  group.add(startLine);

  // --- Walls (visual barriers both sides) -------------------------------
  const walls = new THREE.Group();
  walls.name = 'Walls';
  const wallHeight = 1.2;
  const wallOffset = halfWidth + 1.0; // just outside the kerb stripe (0.6 wide)
  const leftWall = buildWall(pts, +wallOffset, wallHeight, closed);
  const rightWall = buildWall(pts, -wallOffset, wallHeight, closed);
  walls.add(leftWall, rightWall);
  group.add(walls);

  const dispose = (): void => {
    road.geometry.dispose();
    (road.material as THREE.Material).dispose();
    leftKerb.geometry.dispose();
    (leftKerb.material as THREE.Material).dispose();
    rightKerb.geometry.dispose();
    (rightKerb.material as THREE.Material).dispose();
    // Edge glow strips.
    (leftGlow as THREE.Mesh).geometry.dispose();
    ((leftGlow as THREE.Mesh).material as THREE.Material).dispose();
    (rightGlow as THREE.Mesh).geometry.dispose();
    ((rightGlow as THREE.Mesh).material as THREE.Material).dispose();
    if (centerlineLine) {
      centerlineLine.geometry.dispose();
      (centerlineLine.material as THREE.Material).dispose();
    }
    startLine.geometry.dispose();
    (startLine.material as THREE.Material).dispose();
    walls.children.forEach((w) => {
      const m = w as THREE.Mesh;
      m.geometry.dispose();
      (m.material as THREE.Material).dispose();
    });
  };

  return { group, road, centerline: centerlineLine, startLine, walls, dispose };
}
