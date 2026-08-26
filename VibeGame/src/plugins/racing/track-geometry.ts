import * as THREE from 'three';
import { applySurfaceDetail } from '../rendering';
import { createFrame, type TrackFrame, type TrackSpline } from './spline';

/**
 * Track meshes built from a {@link TrackSpline}.
 *
 * Everything is generated as a handful of merged buffer geometries (road,
 * kerbs, walls, apron, start line) rather than a mesh per segment, so a 4 km
 * circuit costs a few draw calls instead of hundreds.
 */
export interface TrackMeshes {
  group: THREE.Group;
  road: THREE.Mesh;
  apron: THREE.Mesh;
  kerbs: THREE.Mesh;
  walls: THREE.Mesh;
  startLine: THREE.Mesh;
  /** Deck box + pylons under the elevated spans; null when nothing flies. */
  viaduct: THREE.Mesh | null;
  dispose: () => void;
}

/**
 * Elevated-span support. A circuit that leaves the ground needs something
 * holding it up: without this the track reads as a ribbon painted on the sky,
 * and the trees and buildings the span flies over make it worse, not better.
 */
export interface ViaductOptions {
  /** Natural ground height (world Y) at a world XZ — usually the terrain. */
  groundYAt: (x: number, z: number) => number;
  /**
   * Deck-above-ground distance (m) that counts as flying. Keep it equal to the
   * `<Road flatten-viaduct-clearance>` used for the carve, or the terrain will
   * be graded under a span that also gets pylons.
   */
  clearance?: number;
  /** Arc spacing between pylons (m). */
  pylonSpacing?: number;
  /** Deck / pylon colour. */
  color?: number;
}

/** Default deck-above-ground distance that counts as a viaduct (m). */
export const DEFAULT_VIADUCT_CLEARANCE = 4;

/** Default arc spacing between pylons (m). */
export const DEFAULT_PYLON_SPACING = 26;

/** Thickness of the deck slab under the road surface (m). */
const DECK_THICKNESS = 1.1;

/** Half-width of a pylon at the deck (m); the base is 1.6× this. */
const PYLON_HALF = 1.15;

/** How far a pylon foot sinks under the ground so it never shows a gap (m). */
const PYLON_EMBED = 1.5;

export interface TrackStyle {
  /** Asphalt tint. */
  road?: number;
  /** Ground either side of the circuit. */
  apron?: number;
  /** Gravel/shoulder strip between the road and the barrier. */
  shoulderColor?: number;
  /** Barrier colour. */
  wall?: number;
  /** Lane marking colour. */
  line?: number;
  /** Surface theme: 'asphalt' (default) or 'holo' (neon grid + emissive rails). */
  theme?: 'asphalt' | 'holo';
}

const DEFAULT_STYLE: Required<TrackStyle> = {
  road: 0x3a3d45,
  apron: 0x4a6b33,
  shoulderColor: 0x8a7a5c,
  wall: 0xd8dae0,
  line: 0xf2f2f2,
  theme: 'asphalt',
};

/** Width of the gravel/kerb strip drawn just outside the racing surface (m). */
const KERB_WIDTH = 0.9;
/** Height of the barrier wall (m). */
const WALL_HEIGHT = 1.15;
/** Thickness of the barrier wall — used for both the mesh and the collision limit. */
const WALL_THICKNESS = 0.9;
/**
 * How far the wall base drops below the driving surface (m). The track is
 * suspended this far above the carved bed (see TRACK_ELEVATION in the
 * examples), so the wall's inner face extends down to the bed and reads as a
 * solid embankment instead of a floating ribbon with a hollow underneath.
 */
const EMBANKMENT_DEPTH = 1.1;
/**
 * How far the embankment slopes away from the barrier (m).
 *
 * Kept modest on purpose: a ribbon wider than the tightest corner radius folds
 * over itself, which is what turned the old 90 m "apron" into a fan of
 * criss-crossing bands. The ground beyond it is a plain flat plane.
 */
const APRON_WIDTH = 18;
/** Curvature above which a corner gets kerbs. */
const KERB_CURVATURE = 0.004;

/**
 * Procedural surface texture. Two variants:
 *
 * - **asphalt**: dark aggregate noise plus a dashed centre line and solid edge
 *   lines. Generating it beats shipping a PNG — it tiles exactly with the
 *   arc-length UVs and costs one 256×512 canvas at boot.
 * - **holo**: a dark base with a neon hex-ish grid, a glowing centre line and
 *   emissive edge rails. The road material also uses this as `emissiveMap`, so
 *   the grid reads as lit-from-within rather than painted on.
 */
function createRoadTexture(
  style: Required<TrackStyle>,
  holo: boolean
): THREE.Texture | null {
  if (typeof document === 'undefined') return null;
  const w = 256;
  const h = 512;
  const canvas = document.createElement('canvas');
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext('2d');
  if (!ctx) return null;

  const base = new THREE.Color(style.road);
  ctx.fillStyle = `#${base.getHexString()}`;
  ctx.fillRect(0, 0, w, h);

  if (holo) {
    // Dark road base, brighter than asphalt so the neon reads.
    ctx.fillStyle = '#10131c';
    ctx.fillRect(0, 0, w, h);
    // Hex-ish grid: two families of diagonal lines.
    ctx.strokeStyle = 'rgba(56,232,255,0.35)';
    ctx.lineWidth = 1.5;
    const gap = 34;
    for (let x = -h; x < w + h; x += gap) {
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x + h, h);
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(x + h, 0);
      ctx.lineTo(x, h);
      ctx.stroke();
    }
    // Glowing centre line (the UV runs 0..1 across the road).
    ctx.strokeStyle = 'rgba(56,232,255,0.85)';
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.moveTo(w * 0.5, 0);
    ctx.lineTo(w * 0.5, h);
    ctx.stroke();
    // Edge rails.
    ctx.fillStyle = 'rgba(255,93,255,0.7)';
    ctx.fillRect(w * 0.035, 0, 4, h);
    ctx.fillRect(w * 0.965 - 4, 0, 4, h);
  } else {
    // Aggregate speckle.
    for (let i = 0; i < 4200; i++) {
      const x = Math.random() * w;
      const y = Math.random() * h;
      const shade = Math.random() * 0.18 - 0.09;
      const c = base.clone().offsetHSL(0, 0, shade);
      ctx.fillStyle = `#${c.getHexString()}`;
      ctx.fillRect(x, y, 1.6, 1.6);
    }

    const line = new THREE.Color(style.line);
    ctx.fillStyle = `#${line.getHexString()}`;
    // Edge lines (the UV runs 0..1 across the road).
    ctx.globalAlpha = 0.85;
    ctx.fillRect(w * 0.035, 0, 3, h);
    ctx.fillRect(w * 0.965 - 3, 0, 3, h);
    // Dashed centre line.
    ctx.globalAlpha = 0.7;
    for (let y = 0; y < h; y += 96) {
      ctx.fillRect(w * 0.5 - 2, y, 4, 52);
    }
    ctx.globalAlpha = 1;
  }

  const tex = new THREE.CanvasTexture(canvas);
  tex.wrapS = THREE.ClampToEdgeWrapping;
  tex.wrapT = THREE.RepeatWrapping;
  tex.anisotropy = 8;
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

interface RibbonOptions {
  /** Lateral offset of the inner edge (m, signed). */
  from: (f: TrackFrame) => number;
  /** Lateral offset of the outer edge (m, signed). */
  to: (f: TrackFrame) => number;
  /** Vertical lift above the surface (m). */
  lift: number;
  /** UV repeat along the track (metres per texture tile); 0 = no V tiling. */
  tile?: number;
  /** Optional per-sample vertex colour. */
  colorAt?: (f: TrackFrame, i: number) => [number, number, number] | null;
}

/** Build a ribbon that follows the spline between two lateral offsets. */
function buildRibbon(
  spline: TrackSpline,
  material: THREE.Material,
  opts: RibbonOptions
): THREE.Mesh {
  const frame = createFrame();
  const count = spline.count;
  const rings = spline.closed ? count + 1 : count;
  const positions = new Float32Array(rings * 2 * 3);
  const uvs = new Float32Array(rings * 2 * 2);
  const colors = opts.colorAt ? new Float32Array(rings * 2 * 3) : null;
  const indices: number[] = [];

  let cum = 0;
  let prevX = 0;
  let prevY = 0;
  let prevZ = 0;

  for (let i = 0; i < rings; i++) {
    const s = (i % count) * spline.step;
    spline.sampleAt(s, frame);
    if (i > 0) {
      cum += Math.hypot(frame.x - prevX, frame.y - prevY, frame.z - prevZ);
    }
    prevX = frame.x;
    prevY = frame.y;
    prevZ = frame.z;

    const a = opts.from(frame);
    const b = opts.to(frame);
    const base = i * 6;
    positions[base] = frame.x + frame.rx * a + frame.ux * opts.lift;
    positions[base + 1] = frame.y + frame.ry * a + frame.uy * opts.lift;
    positions[base + 2] = frame.z + frame.rz * a + frame.uz * opts.lift;
    positions[base + 3] = frame.x + frame.rx * b + frame.ux * opts.lift;
    positions[base + 4] = frame.y + frame.ry * b + frame.uy * opts.lift;
    positions[base + 5] = frame.z + frame.rz * b + frame.uz * opts.lift;

    const v = opts.tile ? cum / opts.tile : cum;
    const uvBase = i * 4;
    uvs[uvBase] = 0;
    uvs[uvBase + 1] = v;
    uvs[uvBase + 2] = 1;
    uvs[uvBase + 3] = v;

    if (colors && opts.colorAt) {
      const c = opts.colorAt(frame, i) ?? [1, 1, 1];
      colors[base] = c[0];
      colors[base + 1] = c[1];
      colors[base + 2] = c[2];
      colors[base + 3] = c[0];
      colors[base + 4] = c[1];
      colors[base + 5] = c[2];
    }

    if (i < rings - 1) {
      // Wind counter-clockwise seen from above: vertex 0 is the left edge and
      // vertex 1 the right edge, so (0, 2, 1) puts the face normal along the
      // surface up vector. The other order points every ribbon at the ground,
      // where backface culling makes the whole road invisible.
      const q = i * 2;
      indices.push(q, q + 2, q + 1, q + 1, q + 2, q + 3);
    }
  }

  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  geo.setAttribute('uv', new THREE.BufferAttribute(uvs, 2));
  if (colors) geo.setAttribute('color', new THREE.BufferAttribute(colors, 3));
  geo.setIndex(indices);
  geo.computeVertexNormals();
  const mesh = new THREE.Mesh(geo, material);
  mesh.receiveShadow = true;
  return mesh;
}

/**
 * Kerb ribbon: red/white blocks, but only around corners. A kerb painted down
 * a 600 m straight is the tell-tale of a generated track; real circuits only
 * kerb the turns.
 */
function buildKerbs(
  spline: TrackSpline,
  shoulder: number,
  holo = false
): THREE.Mesh {
  const frame = createFrame();
  const positions: number[] = [];
  const colors: number[] = [];
  const indices: number[] = [];
  let vertexCount = 0;
  const blockLength = Math.max(spline.step, 2);

  for (let i = 0; i < spline.count; i++) {
    const s = i * spline.step;
    spline.sampleAt(s, frame);
    const curve = frame.curvature;
    if (Math.abs(curve) < KERB_CURVATURE) continue;
    // Kerb on the inside of the corner, and on the outside at the exit.
    const sides =
      Math.abs(curve) > KERB_CURVATURE * 2 ? [-1, 1] : [curve > 0 ? -1 : 1];
    const white = Math.floor(s / blockLength) % 2 === 0;
    const c: [number, number, number] = holo
      ? white
        ? [0.12, 0.02, 0.2]
        : [0.9, 0.15, 0.95]
      : white
        ? [0.92, 0.92, 0.94]
        : [0.85, 0.13, 0.16];

    // Next sample for the block's far edge.
    const nextFrame = createFrame();
    spline.sampleAt(s + spline.step, nextFrame);

    for (const side of sides) {
      const inner = frame.width * 0.5 * side;
      const outer = (frame.width * 0.5 + KERB_WIDTH) * side;
      const innerN = nextFrame.width * 0.5 * side;
      const outerN = (nextFrame.width * 0.5 + KERB_WIDTH) * side;
      const lift = 0.045;
      const pts = [
        [
          frame.x + frame.rx * inner + frame.ux * lift,
          frame.y + frame.ry * inner + frame.uy * lift,
          frame.z + frame.rz * inner + frame.uz * lift,
        ],
        [
          frame.x + frame.rx * outer + frame.ux * lift,
          frame.y + frame.ry * outer + frame.uy * lift,
          frame.z + frame.rz * outer + frame.uz * lift,
        ],
        [
          nextFrame.x + nextFrame.rx * innerN + nextFrame.ux * lift,
          nextFrame.y + nextFrame.ry * innerN + nextFrame.uy * lift,
          nextFrame.z + nextFrame.rz * innerN + nextFrame.uz * lift,
        ],
        [
          nextFrame.x + nextFrame.rx * outerN + nextFrame.ux * lift,
          nextFrame.y + nextFrame.ry * outerN + nextFrame.uy * lift,
          nextFrame.z + nextFrame.rz * outerN + nextFrame.uz * lift,
        ],
      ];
      for (const p of pts) {
        positions.push(p[0]!, p[1]!, p[2]!);
        colors.push(c[0], c[1], c[2]);
      }
      // Winding depends on which side of the road the kerb is on: on the right
      // the inner→outer edge runs along +right, on the left along -right, and
      // the two produce opposite face normals.
      const b = vertexCount;
      if (side > 0) indices.push(b, b + 2, b + 1, b + 1, b + 2, b + 3);
      else indices.push(b, b + 1, b + 2, b + 1, b + 3, b + 2);
      vertexCount += 4;
    }
  }
  void shoulder;

  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geo.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
  geo.setIndex(indices);
  geo.computeVertexNormals();
  const kerbMat = new THREE.MeshStandardMaterial({
    vertexColors: true,
    roughness: 0.75,
    metalness: 0,
    ...(holo ? { emissive: 0xff5dff, emissiveIntensity: 0.7 } : {}),
  });
  if (holo) kerbMat.userData.holo = true;
  return new THREE.Mesh(geo, kerbMat);
}

/** Both barriers as one geometry: a vertical wall with a coloured top rail. */
function buildWalls(
  spline: TrackSpline,
  shoulder: number,
  color: number,
  holo = false
): THREE.Mesh {
  const frame = createFrame();
  const positions: number[] = [];
  const colors: number[] = [];
  const indices: number[] = [];
  let vertexCount = 0;
  const wallColor = new THREE.Color(holo ? 0x0b0e16 : color);
  const railColor = new THREE.Color(holo ? 0x38e8ff : 0xd0242f);
  const rings = spline.closed ? spline.count + 1 : spline.count;

  for (const side of [-1, 1]) {
    const startVertex = vertexCount;
    for (let i = 0; i < rings; i++) {
      spline.sampleAt((i % spline.count) * spline.step, frame);
      // The wall sits at the shoulder edge and projects both inward (under the
      // track surface) and outward to form a solid barrier. The visible wall
      // is a thin ribbon (single face, rendered DoubleSide) that extends
      // upward; depth is conveyed by the top cap rather than by two parallel
      // faces, which avoids z-fighting between the inner and outer planes.
      const innerOff = (frame.width * 0.5 + shoulder) * side;
      const outerOff = innerOff + WALL_THICKNESS * side;
      const ix = frame.x + frame.rx * innerOff;
      const iy = frame.y + frame.ry * innerOff;
      const iz = frame.z + frame.rz * innerOff;
      const ox = frame.x + frame.rx * outerOff;
      const oy = frame.y + frame.ry * outerOff;
      const oz = frame.z + frame.rz * outerOff;

      // Inner face vertices (bed, base, mid rail, top). The bed vertex drops
      // along -up to the carved bed so the wall doubles as the embankment
      // under the suspended track.
      const bedX = ix - frame.ux * EMBANKMENT_DEPTH;
      const bedY = iy - frame.uy * EMBANKMENT_DEPTH;
      const bedZ = iz - frame.uz * EMBANKMENT_DEPTH;
      positions.push(bedX, bedY, bedZ);
      positions.push(ix, iy, iz);
      positions.push(
        ix + frame.ux * WALL_HEIGHT * 0.72,
        iy + frame.uy * WALL_HEIGHT * 0.72,
        iz + frame.uz * WALL_HEIGHT * 0.72
      );
      positions.push(
        ix + frame.ux * WALL_HEIGHT,
        iy + frame.uy * WALL_HEIGHT,
        iz + frame.uz * WALL_HEIGHT
      );
      colors.push(wallColor.r, wallColor.g, wallColor.b);
      colors.push(wallColor.r, wallColor.g, wallColor.b);
      colors.push(wallColor.r, wallColor.g, wallColor.b);
      colors.push(railColor.r, railColor.g, railColor.b);
      vertexCount += 4;
      // Outer top vertices for the cap only (one Y per ring, no body).
      positions.push(ix, iy, iz);
      positions.push(ox, oy, oz);
      positions.push(
        ix + frame.ux * WALL_HEIGHT,
        iy + frame.uy * WALL_HEIGHT,
        iz + frame.uz * WALL_HEIGHT
      );
      positions.push(
        ox + frame.ux * WALL_HEIGHT,
        oy + frame.uy * WALL_HEIGHT,
        oz + frame.uz * WALL_HEIGHT
      );
      // Cap colours: rail red on top, white on the underside.
      for (let k = 0; k < 4; k++)
        colors.push(railColor.r, railColor.g, railColor.b);
      vertexCount += 4;
    }
    for (let i = 0; i < rings - 1; i++) {
      // Inner face: 4 vertices per ring (bed, base, mid, top).
      const a = startVertex + i * 8;
      const b = startVertex + (i + 1) * 8;
      const ib = a;
      const ibs = a + 1;
      const im = a + 2;
      const it = a + 3;
      // Caps: 4 vertices per ring (inner-base, outer-base, inner-top, outer-top).
      const ci_bb = a + 4;
      const co_bb = a + 5;
      const ci_tt = a + 6;
      const co_tt = a + 7;

      // Inner face (DoubleSide covers the outer appearance; we draw one set).
      indices.push(ib, ibs, b, ibs, b + 1, b);
      indices.push(ibs, im, b + 1, im, b + 2, b + 1);
      indices.push(im, it, b + 2, it, b + 3, b + 2);
      // Bottom cap: inner-base → outer-base → next inner-base, etc.
      indices.push(ci_bb, co_bb, b + 4, co_bb, b + 5, b + 4);
      // Top cap strips: inner-top → outer-top → next inner-top, etc.
      indices.push(ci_tt, co_tt, b + 6, co_tt, b + 7, b + 6);
      // Front edge between inner-face top and cap top.
      indices.push(it, ci_tt, b + 3, ci_tt, b + 7, b + 3);
    }
  }

  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geo.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
  geo.setIndex(indices);
  geo.computeVertexNormals();
  const wallMat = new THREE.MeshStandardMaterial({
    vertexColors: true,
    roughness: 0.85,
    metalness: 0.05,
    side: THREE.DoubleSide,
    ...(holo ? { emissive: 0x38e8ff, emissiveIntensity: 0.35 } : {}),
  });
  if (holo) wallMat.userData.holo = true;
  return new THREE.Mesh(geo, wallMat);
}

/** Push an axis-aligned box as 6 independent quads (flat normals). */
function pushBox(
  positions: number[],
  indices: number[],
  cx: number,
  cy: number,
  cz: number,
  topHalf: number,
  halfY: number,
  bottomHalf: number
): void {
  const y0 = cy - halfY;
  const y1 = cy + halfY;
  // Corner XZ at the bottom (wider) and at the top.
  const corners = (h: number): [number, number][] => [
    [cx - h, cz - h],
    [cx + h, cz - h],
    [cx + h, cz + h],
    [cx - h, cz + h],
  ];
  const lo = corners(bottomHalf);
  const hi = corners(topHalf);
  const quad = (
    a: [number, number, number],
    b: [number, number, number],
    c: [number, number, number],
    d: [number, number, number]
  ) => {
    const base = positions.length / 3;
    positions.push(...a, ...b, ...c, ...d);
    indices.push(base, base + 1, base + 2, base, base + 2, base + 3);
  };
  for (let i = 0; i < 4; i++) {
    const j = (i + 1) % 4;
    quad(
      [lo[i]![0], y0, lo[i]![1]],
      [lo[j]![0], y0, lo[j]![1]],
      [hi[j]![0], y1, hi[j]![1]],
      [hi[i]![0], y1, hi[i]![1]]
    );
  }
  quad(
    [hi[0]![0], y1, hi[0]![1]],
    [hi[1]![0], y1, hi[1]![1]],
    [hi[2]![0], y1, hi[2]![1]],
    [hi[3]![0], y1, hi[3]![1]]
  );
}

/**
 * Deck box + pylons for every stretch that flies above the ground.
 *
 * Returns null when the whole circuit sits on the terrain — a normal track pays
 * nothing for this. Spans are found by comparing the banked track surface with
 * the ground sampler, so the author only has to draw the centerline high: the
 * carve leaves the valley alone (`flatten-viaduct-clearance`) and this puts the
 * structure under it.
 */
function buildViaduct(
  spline: TrackSpline,
  shoulder: number,
  opts: ViaductOptions
): THREE.Mesh | null {
  const clearance = opts.clearance ?? DEFAULT_VIADUCT_CLEARANCE;
  const spacing = Math.max(opts.pylonSpacing ?? DEFAULT_PYLON_SPACING, 4);
  const frame = createFrame();
  const positions: number[] = [];
  const indices: number[] = [];

  const rings = spline.closed ? spline.count + 1 : spline.count;
  // Per-ring deck corners + how high the deck flies here.
  const deck: {
    lx: number;
    ly: number;
    lz: number;
    rx: number;
    ry: number;
    rz: number;
    height: number;
    arc: number;
    cx: number;
    cz: number;
    cy: number;
  }[] = [];
  for (let i = 0; i < rings; i++) {
    const s = (i % spline.count) * spline.step;
    spline.sampleAt(s, frame);
    const half = frame.width * 0.5 + shoulder;
    const lx = frame.x - frame.rx * half;
    const ly = frame.y - frame.ry * half - DECK_THICKNESS;
    const lz = frame.z - frame.rz * half;
    const rx = frame.x + frame.rx * half;
    const ry = frame.y + frame.ry * half - DECK_THICKNESS;
    const rz = frame.z + frame.rz * half;
    const ground = opts.groundYAt(frame.x, frame.z);
    deck.push({
      lx,
      ly,
      lz,
      rx,
      ry,
      rz,
      height: frame.y - ground,
      arc: i * spline.step,
      cx: frame.x,
      cz: frame.z,
      cy: frame.y - DECK_THICKNESS,
    });
  }

  const flying = deck.map((d) => d.height > clearance);
  if (!flying.some(Boolean)) return null;

  // Underside slab + side fascia, emitted only between two flying rings.
  const strip = (
    a: [number, number, number],
    b: [number, number, number],
    c: [number, number, number],
    d: [number, number, number]
  ) => {
    const base = positions.length / 3;
    positions.push(...a, ...b, ...c, ...d);
    indices.push(base, base + 1, base + 2, base, base + 2, base + 3);
  };
  for (let i = 0; i + 1 < deck.length; i++) {
    if (!flying[i] || !flying[i + 1]) continue;
    const p = deck[i]!;
    const q = deck[i + 1]!;
    // Underside (seen from below).
    strip(
      [p.lx, p.ly, p.lz],
      [q.lx, q.ly, q.lz],
      [q.rx, q.ry, q.rz],
      [p.rx, p.ry, p.rz]
    );
    // Fascia: from the road edge down to the slab, both sides.
    strip(
      [p.lx, p.ly + DECK_THICKNESS, p.lz],
      [q.lx, q.ly + DECK_THICKNESS, q.lz],
      [q.lx, q.ly, q.lz],
      [p.lx, p.ly, p.lz]
    );
    strip(
      [p.rx, p.ry, p.rz],
      [q.rx, q.ry, q.rz],
      [q.rx, q.ry + DECK_THICKNESS, q.rz],
      [p.rx, p.ry + DECK_THICKNESS, p.rz]
    );
  }

  // A column must not land on the circuit itself. The whole point of flying is
  // that something passes underneath — on this track the mountain descent dives
  // under the basin span — and a pylon dropped on that road is a wall in the
  // middle of a straight. Candidates that would block another pass of the track
  // are skipped; the next station along carries the load instead.
  const blocksTrack = (x: number, z: number, arc: number): boolean => {
    const half = spline.length * 0.5;
    for (let i = 0; i < spline.count; i++) {
      const s = i * spline.step;
      let dArc = Math.abs(s - arc);
      if (spline.closed && dArc > half) dArc = spline.length - dArc;
      // Ignore the span's own stations — it is the road overhead, not under.
      if (dArc < spacing * 2) continue;
      spline.sampleAt(s, frame);
      const clear = frame.width * 0.5 + shoulder + PYLON_HALF * 2;
      const dx = x - frame.x;
      const dz = z - frame.z;
      if (dx * dx + dz * dz < clear * clear) return true;
    }
    return false;
  };

  // Pylons: one every `spacing` metres of arc inside each flying run, always
  // one at the tallest point so a long span is never held by its ends alone.
  let nextArc = -Infinity;
  for (let i = 0; i < deck.length; i++) {
    if (!flying[i]) {
      nextArc = -Infinity;
      continue;
    }
    const d = deck[i]!;
    if (nextArc === -Infinity) nextArc = d.arc + spacing * 0.5;
    if (d.arc < nextArc) continue;
    if (blocksTrack(d.cx, d.cz, d.arc)) continue;
    nextArc = d.arc + spacing;
    const ground = d.cy + DECK_THICKNESS - d.height;
    const foot = ground - PYLON_EMBED;
    const halfY = (d.cy - foot) * 0.5;
    if (halfY <= 0.2) continue;
    pushBox(
      positions,
      indices,
      d.cx,
      foot + halfY,
      d.cz,
      PYLON_HALF,
      halfY,
      PYLON_HALF * 1.6
    );
  }

  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geo.setIndex(indices);
  geo.computeVertexNormals();
  const mat = new THREE.MeshStandardMaterial({
    color: opts.color ?? 0x6f7480,
    roughness: 0.9,
    metalness: 0.05,
    side: THREE.DoubleSide,
  });
  const mesh = new THREE.Mesh(geo, mat);
  mesh.name = 'TrackViaduct';
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  return mesh;
}

/**
 * The embankment: a skirt from the outside of the barrier down to the ground
 * plane. It is what makes an elevated section look like it is *on* something
 * instead of hovering, and it hides the seam where the circuit meets the world.
 */
function buildEmbankment(
  spline: TrackSpline,
  shoulder: number,
  groundY: number,
  color: number
): THREE.Mesh {
  const frame = createFrame();
  const rings = spline.closed ? spline.count + 1 : spline.count;
  const positions = new Float32Array(rings * 2 * 3 * 2);
  const indices: number[] = [];
  let vertex = 0;

  for (const side of [-1, 1]) {
    const start = vertex;
    for (let i = 0; i < rings; i++) {
      spline.sampleAt((i % spline.count) * spline.step, frame);
      const inner = (frame.width * 0.5 + shoulder) * side;
      const ix = frame.x + frame.rx * inner;
      const iy = frame.y + frame.ry * inner - 0.05;
      const iz = frame.z + frame.rz * inner;
      // The outer edge runs out horizontally and drops to the ground plane, so
      // the slope steepens automatically on the high parts of the circuit.
      const outer = (frame.width * 0.5 + shoulder + APRON_WIDTH) * side;
      const ox = frame.x + frame.rx * outer;
      const oz = frame.z + frame.rz * outer;
      const base = vertex * 3;
      positions[base] = ix;
      positions[base + 1] = iy;
      positions[base + 2] = iz;
      positions[base + 3] = ox;
      positions[base + 4] = Math.min(iy, groundY);
      positions[base + 5] = oz;
      vertex += 2;
    }
    for (let i = 0; i < rings - 1; i++) {
      const a = start + i * 2;
      const b = start + (i + 1) * 2;
      if (side > 0) indices.push(a, a + 1, b, a + 1, b + 1, b);
      else indices.push(a, b, a + 1, a + 1, b, b + 1);
    }
  }

  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  geo.setIndex(indices);
  geo.computeVertexNormals();
  const mesh = new THREE.Mesh(
    geo,
    new THREE.MeshStandardMaterial({
      color,
      roughness: 1,
      metalness: 0,
      side: THREE.DoubleSide,
    })
  );
  mesh.receiveShadow = true;
  return mesh;
}

/** Flat ground under everything, sized to the circuit's bounding box. */
function buildGround(
  spline: TrackSpline,
  groundY: number,
  color: number
): THREE.Mesh {
  const b = spline.bounds();
  const margin = 600;
  const width = b.maxX - b.minX + margin * 2;
  const depth = b.maxZ - b.minZ + margin * 2;
  const geo = new THREE.PlaneGeometry(width, depth, 1, 1);
  geo.rotateX(-Math.PI / 2);
  geo.translate((b.minX + b.maxX) / 2, groundY, (b.minZ + b.maxZ) / 2);
  const mesh = new THREE.Mesh(
    geo,
    new THREE.MeshStandardMaterial({ color, roughness: 1, metalness: 0 })
  );
  mesh.receiveShadow = true;
  mesh.name = 'TrackGround';
  return mesh;
}

/** Chequered start/finish strip laid across the road at s = 0. */
function buildStartLine(spline: TrackSpline): THREE.Mesh {
  const frame = createFrame();
  spline.sampleAt(0, frame);
  const width = frame.width;
  const depth = 2.2;
  const cols = Math.max(8, Math.round(width / 1.2));
  const rows = 2;
  const positions: number[] = [];
  const colors: number[] = [];
  const indices: number[] = [];
  let vertexCount = 0;
  const lift = 0.05;

  for (let c = 0; c < cols; c++) {
    for (let r = 0; r < rows; r++) {
      const lat0 = -width / 2 + (c / cols) * width;
      const lat1 = -width / 2 + ((c + 1) / cols) * width;
      const s0 = -depth / 2 + (r / rows) * depth;
      const s1 = -depth / 2 + ((r + 1) / rows) * depth;
      const dark = (c + r) % 2 === 0;
      const shade = dark ? 0.08 : 0.95;
      const corners: [number, number][] = [
        [s0, lat0],
        [s0, lat1],
        [s1, lat0],
        [s1, lat1],
      ];
      for (const [ds, lat] of corners) {
        const f = spline.sampleAt(spline.wrapS(ds), createFrame());
        positions.push(
          f.x + f.rx * lat + f.ux * lift,
          f.y + f.ry * lat + f.uy * lift,
          f.z + f.rz * lat + f.uz * lift
        );
        colors.push(shade, shade, shade);
      }
      const b = vertexCount;
      indices.push(b, b + 2, b + 1, b + 1, b + 2, b + 3);
      vertexCount += 4;
    }
  }

  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geo.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
  geo.setIndex(indices);
  geo.computeVertexNormals();
  return new THREE.Mesh(
    geo,
    new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.6 })
  );
}

/**
 * Build the full circuit: grass apron, gravel shoulders, asphalt, kerbs,
 * barriers and the start line — all from the spline, all banked with it.
 */
export function buildTrackMeshes(
  spline: TrackSpline,
  shoulder: number,
  style: TrackStyle = {},
  viaductOpts?: ViaductOptions
): TrackMeshes {
  const s = { ...DEFAULT_STYLE, ...style };
  const group = new THREE.Group();
  group.name = 'Track';

  // Ground plane, then an embankment skirt from the barrier down to it. A
  // single wide ribbon cannot do this job: on any corner tighter than its own
  // half-width it folds through itself.
  let lowest = Number.POSITIVE_INFINITY;
  spline.forEachSample((_i, _x, y) => {
    if (y < lowest) lowest = y;
  });
  const groundY = (Number.isFinite(lowest) ? lowest : 0) - 2.5;

  const ground = buildGround(spline, groundY, s.apron);
  ground.renderOrder = -3;
  group.add(ground);

  const apron = buildEmbankment(spline, shoulder, groundY, s.apron);
  apron.name = 'TrackApron';
  apron.renderOrder = -2;
  group.add(apron);

  // Gravel shoulders (both sides in one ribbon each).
  const shoulderMat = new THREE.MeshStandardMaterial({
    color: s.shoulderColor,
    roughness: 1,
    metalness: 0,
  });
  // The ribbon's V counts metres down the circuit, so the along-track repeat
  // has to be tiny where the across-track one is single digits.
  applySurfaceDetail(shoulderMat, 'gravel', {
    repeatX: 3,
    repeatY: 0.5,
    normalScale: 0.9,
  });
  if (shoulder > 0.05) {
    const left = buildRibbon(spline, shoulderMat, {
      from: (f) => -(f.width * 0.5 + shoulder),
      to: (f) => -f.width * 0.5,
      lift: -0.02,
    });
    const right = buildRibbon(spline, shoulderMat, {
      from: (f) => f.width * 0.5,
      to: (f) => f.width * 0.5 + shoulder,
      lift: -0.02,
    });
    left.name = 'TrackShoulderLeft';
    right.name = 'TrackShoulderRight';
    group.add(left, right);
  }

  // Asphalt / holo surface.
  const holo = s.theme === 'holo';
  const roadTexture = createRoadTexture(s, holo);
  const roadMat = new THREE.MeshStandardMaterial({
    color: roadTexture ? 0xffffff : s.road,
    map: roadTexture,
    ...(holo && roadTexture
      ? {
          emissive: 0x38e8ff,
          emissiveMap: roadTexture,
          emissiveIntensity: 0.55,
        }
      : {}),
    roughness: 0.62,
    metalness: 0.02,
  });
  if (holo) (roadMat as THREE.MeshStandardMaterial).userData.holo = true;
  // Wet-premium sheen: the sky IBL grazing-angle reflection is what sells
  // "asphalt" from a chase camera — a touch more of it reads as freshly
  // sealed tarmac, not a mirror (the surface-detail roughness map keeps the
  // grain).
  if (!holo) {
    (roadMat as THREE.MeshStandardMaterial).envMapIntensity = 1.25;
  }
  // Asphalt grain + polished wheel tracks. Without it the road is a flat
  // painted ribbon: the sun's specular lobe is identical over the whole
  // surface, which is exactly what plastic looks like. The holo theme keeps
  // its emissive-clean look — grain there would fight the neon.
  if (!holo) {
    applySurfaceDetail(roadMat, 'asphalt', {
      // U spans the full road width (~12 m), V one tile per 14 m.
      repeatX: 6,
      repeatY: 1,
      roughness: 0.58,
    });
  }
  const road = buildRibbon(spline, roadMat, {
    from: (f) => -f.width * 0.5,
    to: (f) => f.width * 0.5,
    lift: 0,
    tile: 14,
  });
  road.name = 'RoadSurface';
  // Tarmac is not a mirror, but it is the largest surface on screen and it
  // does reflect at grazing angles — which is precisely the angle a chase
  // camera looks at it from. The reflection pass weights the result by
  // roughness and Fresnel, so flagging it costs a soft sheen ahead of the car,
  // not a skating rink. (The auto-detect only picks polished materials, so a
  // road at roughness 0.58 would never opt itself in.)
  road.userData.ssrReflective = true;
  group.add(road);

  const kerbs = buildKerbs(spline, shoulder, holo);
  kerbs.name = 'TrackKerbs';
  group.add(kerbs);

  const walls = buildWalls(spline, shoulder, s.wall, holo);
  walls.name = 'TrackWalls';
  group.add(walls);

  const startLine = buildStartLine(spline);
  startLine.name = 'StartLine';
  group.add(startLine);

  const viaduct = viaductOpts
    ? buildViaduct(spline, shoulder, viaductOpts)
    : null;
  if (viaduct) group.add(viaduct);

  const dispose = (): void => {
    group.traverse((obj) => {
      const mesh = obj as THREE.Mesh;
      if (mesh.isMesh) {
        mesh.geometry?.dispose();
        const mat = mesh.material as THREE.Material | THREE.Material[];
        if (Array.isArray(mat)) mat.forEach((m) => m.dispose());
        else mat?.dispose();
      }
    });
    roadTexture?.dispose();
  };

  return { group, road, apron, kerbs, walls, startLine, viaduct, dispose };
}
