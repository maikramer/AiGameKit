import * as THREE from 'three';
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
  dispose: () => void;
}

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
}

const DEFAULT_STYLE: Required<TrackStyle> = {
  road: 0x3a3d45,
  apron: 0x4a6b33,
  shoulderColor: 0x8a7a5c,
  wall: 0xd8dae0,
  line: 0xf2f2f2,
};

/** Width of the gravel/kerb strip drawn just outside the racing surface (m). */
const KERB_WIDTH = 0.9;
/** Height of the barrier wall (m). */
const WALL_HEIGHT = 1.15;
/** Thickness of the barrier wall — used for both the mesh and the collision limit. */
const WALL_THICKNESS = 0.9;
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
 * Procedural asphalt texture: dark aggregate noise plus a dashed centre line
 * and solid edge lines. Generating it beats shipping a PNG — it tiles exactly
 * with the arc-length UVs and costs one 256×512 canvas at boot.
 */
function createRoadTexture(style: Required<TrackStyle>): THREE.Texture | null {
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
function buildKerbs(spline: TrackSpline, shoulder: number): THREE.Mesh {
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
    const c: [number, number, number] = white
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
  return new THREE.Mesh(
    geo,
    new THREE.MeshStandardMaterial({
      vertexColors: true,
      roughness: 0.75,
      metalness: 0,
    })
  );
}

/** Both barriers as one geometry: a vertical wall with a coloured top rail. */
function buildWalls(
  spline: TrackSpline,
  shoulder: number,
  color: number
): THREE.Mesh {
  const frame = createFrame();
  const positions: number[] = [];
  const colors: number[] = [];
  const indices: number[] = [];
  let vertexCount = 0;
  const wallColor = new THREE.Color(color);
  const railColor = new THREE.Color(0xd0242f);
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

      // Inner face vertices (base, mid rail, top).
      for (const [x, y, z] of [
        [ix, iy, iz],
      ]) {
        positions.push(x, y, z);
        positions.push(
          x + frame.ux * WALL_HEIGHT * 0.72,
          y + frame.uy * WALL_HEIGHT * 0.72,
          z + frame.uz * WALL_HEIGHT * 0.72
        );
        positions.push(
          x + frame.ux * WALL_HEIGHT,
          y + frame.uy * WALL_HEIGHT,
          z + frame.uz * WALL_HEIGHT
        );
        colors.push(wallColor.r, wallColor.g, wallColor.b);
        colors.push(wallColor.r, wallColor.g, wallColor.b);
        colors.push(railColor.r, railColor.g, railColor.b);
        vertexCount += 3;
      }
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
      for (let k = 0; k < 4; k++) colors.push(railColor.r, railColor.g, railColor.b);
      vertexCount += 4;
    }
    for (let i = 0; i < rings - 1; i++) {
      // Inner face: 3 vertices per ring (base, mid, top).
      const a = startVertex + i * 7;
      const b = startVertex + (i + 1) * 7;
      const ib = a;
      const im = a + 1;
      const it = a + 2;
      // Top cap: 4 vertices per ring (inner-base, outer-base, inner-top, outer-top).
      const ci_bb = a + 3;
      const co_bb = a + 4;
      const ci_tt = a + 5;
      const co_tt = a + 6;

      // Inner face (DoubleSide covers the outer appearance; we draw one set).
      indices.push(ib, im, b, im, b + 2, b);
      indices.push(im, it, b + 2, it, b + 4, b + 2);
      // Top cap strips: inner-base → outer-base → next inner-base, etc.
      indices.push(ci_bb, co_bb, b + 3, co_bb, b + 4, b + 3);
      indices.push(ci_tt, co_tt, b + 5, co_tt, b + 6, b + 5);
      // Front edge between inner-face top and cap top.
      indices.push(it, ci_tt, b + 2, ci_tt, b + 5, b + 2);
    }
  }

  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geo.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
  geo.setIndex(indices);
  geo.computeVertexNormals();
  return new THREE.Mesh(
    geo,
    new THREE.MeshStandardMaterial({
      vertexColors: true,
      roughness: 0.85,
      metalness: 0.05,
      side: THREE.DoubleSide,
    })
  );
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
  style: TrackStyle = {}
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

  // Asphalt.
  const roadTexture = createRoadTexture(s);
  const roadMat = new THREE.MeshStandardMaterial({
    color: roadTexture ? 0xffffff : s.road,
    map: roadTexture,
    roughness: 0.62,
    metalness: 0.02,
  });
  const road = buildRibbon(spline, roadMat, {
    from: (f) => -f.width * 0.5,
    to: (f) => f.width * 0.5,
    lift: 0,
    tile: 14,
  });
  road.name = 'RoadSurface';
  group.add(road);

  const kerbs = buildKerbs(spline, shoulder);
  kerbs.name = 'TrackKerbs';
  group.add(kerbs);

  const walls = buildWalls(spline, shoulder, s.wall);
  walls.name = 'TrackWalls';
  group.add(walls);

  const startLine = buildStartLine(spline);
  startLine.name = 'StartLine';
  group.add(startLine);

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

  return { group, road, apron, kerbs, walls, startLine, dispose };
}
