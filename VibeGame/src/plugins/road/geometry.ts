import * as THREE from 'three';

/**
 * Geometria de estrada: ribbon ao longo de uma polyline suavizada, com a
 * textura a fluir e a curvar com a estrada (UV.v = arc-length, UV.u =
 * posição lateral, ambos em metros / texture-scale) e bordas orgânicas por
 * vertex alpha (feather lateral + ruído + feather nas pontas).
 *
 * Tudo determinístico e puro — testável sem GPU.
 */

export interface RoadGeometryOptions {
  /** Largura total da faixa (m). */
  width: number;
  /**
   * Optional per-station width (m) for junction fusion tapers. Receives arc
   * length from the start and total path length. Defaults to constant `width`.
   */
  widthAt?: (arc: number, totalLen: number) => number;
  /** Metros de mundo por tile de textura. */
  textureScale: number;
  /** Fade lateral borda→núcleo (m). */
  edgeFeather: number;
  /** Amplitude do ruído que corrói a borda para dentro (m). */
  edgeNoise: number;
  /** Fade longitudinal no início/fim do path (m; 0 = ponta sólida). */
  endFeatherStart: number;
  endFeatherEnd: number;
  /** Altura do mundo em cada (x,z); default plano y=0. */
  heightAt?: (x: number, z: number) => number;
  /** Elevação extra acima de heightAt (m). */
  yOffset?: number;
  /** Seed do ruído (default: derivado do primeiro ponto do path). */
  seed?: number;
}

/** Hash determinístico → [0,1). */
function hash1(i: number, seed: number): number {
  const s = Math.sin(i * 127.1 + seed * 74.7) * 43758.5453;
  return s - Math.floor(s);
}

/** Value noise 1D (coordenada em unidades de "célula"). */
function valueNoise1(t: number, seed: number): number {
  const i = Math.floor(t);
  const f = t - i;
  const u = f * f * (3 - 2 * f);
  const a = hash1(i, seed);
  const b = hash1(i + 1, seed);
  return a + (b - a) * u;
}

/**
 * Suavização Chaikin (corner cutting) preservando os extremos. Cada iteração
 * substitui cada canto interior por dois pontos a 1/4 e 3/4 do segmento —
 * 2–3 iterações transformam uma polyline autorada em curva suave por onde a
 * textura flui sem vincos.
 */
export function smoothPath(path: number[], iterations: number): number[] {
  let pts = path;
  for (let it = 0; it < iterations; it++) {
    const n = pts.length / 2;
    if (n < 3) return pts;
    const out: number[] = [pts[0]!, pts[1]!];
    for (let i = 0; i < n - 1; i++) {
      const ax = pts[i * 2]!;
      const az = pts[i * 2 + 1]!;
      const bx = pts[(i + 1) * 2]!;
      const bz = pts[(i + 1) * 2 + 1]!;
      out.push(ax + (bx - ax) * 0.25, az + (bz - az) * 0.25);
      out.push(ax + (bx - ax) * 0.75, az + (bz - az) * 0.75);
    }
    out.push(pts[(n - 1) * 2]!, pts[(n - 1) * 2 + 1]!);
    pts = out;
  }
  return pts;
}

/** Shortest XZ distance from `(x, z)` to a polyline `[x0,z0,...]`. */
export function distanceToPolyline(
  path: number[],
  x: number,
  z: number
): number {
  let best = Infinity;
  for (let i = 0; i + 3 < path.length; i += 2) {
    const ax = path[i]!;
    const az = path[i + 1]!;
    const dx = path[i + 2]! - ax;
    const dz = path[i + 3]! - az;
    const lenSq = dx * dx + dz * dz;
    let t = lenSq > 0 ? ((x - ax) * dx + (z - az) * dz) / lenSq : 0;
    t = t < 0 ? 0 : t > 1 ? 1 : t;
    const d = Math.hypot(x - (ax + t * dx), z - (az + t * dz));
    if (d < best) best = d;
  }
  return best;
}

/**
 * Extrapolate the polyline past its ends along the end tangents. Used at
 * junctions: a ribbon that stops exactly on its neighbour's centerline leaves a
 * wedge of bare ground on the outer corner, since neither ribbon covers it.
 */
export function extendPathEnds(
  path: number[],
  startAmount: number,
  endAmount: number
): number[] {
  if (path.length < 4) return path;
  const out = path.slice();
  if (startAmount > 0) {
    const dx = out[0]! - out[2]!;
    const dz = out[1]! - out[3]!;
    const len = Math.hypot(dx, dz) || 1;
    out.unshift(
      out[0]! + (dx / len) * startAmount,
      out[1]! + (dz / len) * startAmount
    );
  }
  if (endAmount > 0) {
    const n = out.length;
    const dx = out[n - 2]! - out[n - 4]!;
    const dz = out[n - 1]! - out[n - 3]!;
    const len = Math.hypot(dx, dz) || 1;
    out.push(
      out[n - 2]! + (dx / len) * endAmount,
      out[n - 1]! + (dz / len) * endAmount
    );
  }
  return out;
}

/** Reamostra a polyline a intervalos ~spacing, preservando os nós originais. */
export function resampleRoadPath(path: number[], spacing: number): number[] {
  const step = Math.max(0.2, spacing);
  const out: number[] = [path[0]!, path[1]!];
  for (let i = 0; i + 3 < path.length; i += 2) {
    const ax = path[i]!;
    const az = path[i + 1]!;
    const bx = path[i + 2]!;
    const bz = path[i + 3]!;
    const len = Math.hypot(bx - ax, bz - az);
    const steps = Math.max(1, Math.ceil(len / step));
    for (let s = 1; s <= steps; s++) {
      const f = s / steps;
      out.push(ax + (bx - ax) * f, az + (bz - az) * f);
    }
  }
  return out;
}

/**
 * Insert midpoints where walk-surface height at the segment mid differs from
 * the linear chord by more than `maxErr`. Ribbon stays on `heightAt` (CCT /
 * heightfield) — never lifts above it — while killing sand wedges under
 * coarse chords between stations.
 */
export function densifyPathByHeight(
  path: number[],
  heightAt: (x: number, z: number) => number,
  maxErr = 0.02,
  maxPasses = 5
): number[] {
  if (path.length < 4) return path;
  let pts = path;
  for (let pass = 0; pass < maxPasses; pass++) {
    const out: number[] = [pts[0]!, pts[1]!];
    let inserted = 0;
    for (let i = 0; i + 3 < pts.length; i += 2) {
      const ax = pts[i]!;
      const az = pts[i + 1]!;
      const bx = pts[i + 2]!;
      const bz = pts[i + 3]!;
      const mx = (ax + bx) * 0.5;
      const mz = (az + bz) * 0.5;
      const ha = heightAt(ax, az);
      const hb = heightAt(bx, bz);
      const hm = heightAt(mx, mz);
      const chord = (ha + hb) * 0.5;
      if (Math.abs(hm - chord) > maxErr) {
        out.push(mx, mz);
        inserted++;
      }
      out.push(bx, bz);
    }
    pts = out;
    if (inserted === 0) break;
  }
  return pts;
}

const smooth01 = (a: number): number => a * a * (3 - 2 * a);

/**
 * Cap on the miter scale at a corner. Without a limit a hairpin would shoot the
 * outer edge to infinity; 3 keeps 90° joins square-ish and clamps tighter ones.
 */
export const ROAD_MITER_LIMIT = 3;

/**
 * Constrói o ribbon da estrada. 4 vértices por estação
 * (bordaEsq, núcleoEsq, núcleoDir, bordaDir) com alpha [0,1,1,0] no atributo
 * `color` (RGBA — o renderer ativa vertex alpha com itemSize 4). A borda de
 * cada estação é corroída para dentro por ruído 1D ao longo do arco (lados
 * independentes), e o feather das pontas multiplica o alpha do núcleo.
 *
 * O `path` deve vir já suavizado/reamostrado (smoothPath + resampleRoadPath).
 */
export function makeRoadGeometry(
  path: number[],
  opts: RoadGeometryOptions
): THREE.BufferGeometry {
  if (path.length < 4) {
    throw new Error('makeRoadGeometry: path precisa de ≥ 2 pontos (4 números)');
  }
  const nodeCount = path.length / 2;
  const baseHalf = Math.max(opts.width, 0.1) / 2;
  const feather = Math.max(opts.edgeFeather, 0);
  const noiseAmp = Math.max(opts.edgeNoise, 0);
  const scale = Math.max(opts.textureScale, 0.01);
  const yOffset = opts.yOffset ?? 0;
  const heightAt = opts.heightAt ?? (() => 0);
  const seed = opts.seed ?? path[0]! * 13.13 + path[1]! * 7.77;
  const widthAt = opts.widthAt;

  // Comprimento total (para o feather da ponta final).
  let totalLen = 0;
  for (let i = 0; i < nodeCount - 1; i++) {
    totalLen += Math.hypot(
      path[(i + 1) * 2]! - path[i * 2]!,
      path[(i + 1) * 2 + 1]! - path[i * 2 + 1]!
    );
  }

  const positions: number[] = [];
  const uvs: number[] = [];
  const colors: number[] = [];
  const indices: number[] = [];

  let arc = 0;
  for (let i = 0; i < nodeCount; i++) {
    const x = path[i * 2]!;
    const z = path[i * 2 + 1]!;
    const half = widthAt ? Math.max(widthAt(arc, totalLen), 0.1) / 2 : baseHalf;

    // Normal miter: média das normais dos segmentos vizinhos (curvas sem
    // gaps; mesma abordagem do river-geometry).
    let nx = 0;
    let nz = 0;
    let segNx = 0;
    let segNz = 0;
    if (i > 0) {
      const dx = x - path[(i - 1) * 2]!;
      const dz = z - path[(i - 1) * 2 + 1]!;
      const len = Math.hypot(dx, dz) || 1;
      segNx = -dz / len;
      segNz = dx / len;
      nx += segNx;
      nz += segNz;
    }
    if (i < nodeCount - 1) {
      const dx = path[(i + 1) * 2]! - x;
      const dz = path[(i + 1) * 2 + 1]! - z;
      const len = Math.hypot(dx, dz) || 1;
      if (i === 0) {
        segNx = -dz / len;
        segNz = dx / len;
      }
      nx += -dz / len;
      nz += dx / len;
    }
    const nlen = Math.hypot(nx, nz) || 1;
    nx /= nlen;
    nz /= nlen;

    // Miter scale: offsetting by `half` along the bisector narrows the ribbon
    // to `half·cos(θ/2)` measured perpendicular to each segment, so corners
    // pinch and the road looks like it breaks where it bends. Dividing by
    // cos(θ/2) (= bisector · segment normal) restores a constant width.
    const cosHalf = Math.abs(nx * segNx + nz * segNz);
    const miter = Math.min(ROAD_MITER_LIMIT, cosHalf > 1e-3 ? 1 / cosHalf : 1);

    // Erosão orgânica da borda, por lado (wavelengths ~2.2 m e ~0.8 m).
    const noiseL =
      noiseAmp > 0
        ? noiseAmp *
          (0.7 * valueNoise1(arc / 2.2, seed) +
            0.3 * valueNoise1(arc / 0.8, seed + 31.7))
        : 0;
    const noiseR =
      noiseAmp > 0
        ? noiseAmp *
          (0.7 * valueNoise1(arc / 2.2, seed + 57.3) +
            0.3 * valueNoise1(arc / 0.8, seed + 88.1))
        : 0;

    const outerL = -(half - noiseL);
    const outerR = half - noiseR;
    // Núcleo opaco (clamp para não cruzar em estradas muito estreitas).
    const featherEff = Math.max(feather, 0.001);
    const coreL = Math.min(outerL + featherEff, -0.02);
    const coreR = Math.max(outerR - featherEff, 0.02);

    // Feather das pontas: multiplica o alpha do núcleo (0 = ponta sólida).
    let endFactor = 1;
    if (opts.endFeatherStart > 0) {
      endFactor *= smooth01(Math.min(arc / opts.endFeatherStart, 1));
    }
    if (opts.endFeatherEnd > 0) {
      endFactor *= smooth01(Math.min((totalLen - arc) / opts.endFeatherEnd, 1));
    }

    // Cross-section shares the centerline height. Sampling heightAt at each
    // lateral lets falloff/dune sides lift the ribbon edges into the sand mesh
    // → coplanar z-fight (rectangular sand LOD tiles when close). CCT walks
    // the corridor centerline; the decal must too.
    const y = heightAt(x, z) + yOffset;
    const laterals = [outerL, coreL, coreR, outerR];
    const alphas = [0, endFactor, endFactor, 0];
    for (let k = 0; k < 4; k++) {
      const lat = laterals[k]!;
      const vx = x + nx * lat * miter;
      const vz = z + nz * lat * miter;
      positions.push(vx, y, vz);
      uvs.push((lat + half) / scale, arc / scale);
      colors.push(1, 1, 1, alphas[k]!);
    }

    if (i < nodeCount - 1) {
      arc += Math.hypot(path[(i + 1) * 2]! - x, path[(i + 1) * 2 + 1]! - z);
    }
  }

  // 3 quads per station pair. Keep camera-facing winding (FrontSide cull uses
  // winding, not the normal attribute). Lighting normals are set below from
  // the heightfield — flipping winding to “fix” computeVertexNormals culled
  // the whole ribbon and the road vanished.
  for (let i = 0; i < nodeCount - 1; i++) {
    const a = i * 4;
    const b = (i + 1) * 4;
    for (let k = 0; k < 3; k++) {
      indices.push(a + k, a + k + 1, b + k, a + k + 1, b + k + 1, b + k);
    }
  }

  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geo.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2));
  geo.setAttribute('color', new THREE.Float32BufferAttribute(colors, 4));
  geo.setIndex(indices);

  // Normals from the shared heightfield (same idea as terrain frontier verts),
  // not computeVertexNormals — densify/LOD Y kinks were creasing the ribbon
  // into dark bands exactly where chunk edges cross the road.
  const normals = new Float32Array(positions.length);
  const e = 0.35;
  for (let i = 0; i < nodeCount; i++) {
    const x = path[i * 2]!;
    const z = path[i * 2 + 1]!;
    const hL = heightAt(x - e, z);
    const hR = heightAt(x + e, z);
    const hD = heightAt(x, z - e);
    const hU = heightAt(x, z + e);
    let nx = (hL - hR) / (2 * e);
    let ny = 1;
    let nz = (hD - hU) / (2 * e);
    const inv = 1 / Math.hypot(nx, ny, nz);
    nx *= inv;
    ny *= inv;
    nz *= inv;
    for (let k = 0; k < 4; k++) {
      const vi = (i * 4 + k) * 3;
      normals[vi] = nx;
      normals[vi + 1] = ny;
      normals[vi + 2] = nz;
    }
  }
  geo.setAttribute('normal', new THREE.Float32BufferAttribute(normals, 3));
  return geo;
}
