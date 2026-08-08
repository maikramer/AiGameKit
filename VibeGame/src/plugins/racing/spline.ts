/**
 * TrackSpline — the geometric authority for a racing circuit.
 *
 * Everything in the racing plugin (road mesh, vehicle grounding, lap counting,
 * AI racing line, minimap, prop placement) reads the circuit through this one
 * object, so they can never disagree about where the road is.
 *
 * Design notes:
 *
 * - **3D, not XZ.** The previous implementation projected onto a flat XZ
 *   polyline, so a circuit that passes over itself (a flyover, a mountain
 *   section crossing the start straight) sampled the *wrong* branch and
 *   teleported cars vertically. Here projection is a full 3D nearest-point
 *   search, and callers pass the previous arc position as a hint so the search
 *   stays on the branch the car is actually driving.
 *
 * - **Uniform arc-length resampling.** Control nodes are interpolated with
 *   Catmull-Rom, then resampled at a fixed spacing (`step`). That makes
 *   `sampleAt(s)` an O(1) array index instead of a search, and gives every
 *   downstream system a stable "metres along the track" coordinate.
 *
 * - **Frames carry banking.** Each sample stores tangent / right / up already
 *   rotated by the local bank angle, so a car placed on the surface leans with
 *   the road for free.
 */

const TWO_PI = Math.PI * 2;

/** A hand-authored control point of the circuit. */
export interface TrackNode {
  x: number;
  y: number;
  z: number;
  /** Full road width at this node (m). Defaults to the track's `width`. */
  width?: number;
  /**
   * Banking in degrees (positive = the road leans so the left edge is higher,
   * i.e. banked for a right-hand turn). Omitted → derived from curvature.
   */
  bank?: number;
  /** Theme tag used by prop dressing and surface palettes. */
  section?: string;
  /** Grip multiplier of the road surface here (1 = dry asphalt). */
  grip?: number;
}

export interface TrackSplineOptions {
  /** Default full road width (m) when a node doesn't override it. */
  width?: number;
  /** Closed circuit (last node connects back to the first). */
  closed?: boolean;
  /** Arc-length spacing of the resampled polyline (m). */
  step?: number;
  /** Max auto-derived banking (degrees) on the tightest corner. */
  maxAutoBank?: number;
  /** Curvature (1/m) that reaches `maxAutoBank`. */
  autoBankCurvature?: number;
}

/** A resolved point on the track surface, with its local frame. */
export interface TrackFrame {
  /** Arc position (m) this frame was sampled at. */
  s: number;
  /** Surface point on the centerline. */
  x: number;
  y: number;
  z: number;
  /** Unit tangent (direction of travel). */
  tx: number;
  ty: number;
  tz: number;
  /** Unit right vector (banked). */
  rx: number;
  ry: number;
  rz: number;
  /** Unit up vector (banked surface normal). */
  ux: number;
  uy: number;
  uz: number;
  /** Full road width here (m). */
  width: number;
  /** Signed curvature (1/m); positive = turning left. */
  curvature: number;
  /** Bank angle (radians). */
  bank: number;
  /** Surface grip multiplier. */
  grip: number;
  /** Theme tag. */
  section: string;
}

/** Result of projecting a world point onto the track. */
export interface TrackProjection {
  /** Arc position of the nearest centerline point (m). */
  s: number;
  /** Signed lateral offset from the centerline (m); positive = right of travel. */
  lateral: number;
  /** Height above the road surface along the surface normal (m). */
  height: number;
  /** Distance from the surface point, for confidence checks. */
  distance: number;
}

/** Mutable frame reused by hot paths that don't want to allocate. */
export function createFrame(): TrackFrame {
  return {
    s: 0,
    x: 0,
    y: 0,
    z: 0,
    tx: 0,
    ty: 0,
    tz: 1,
    rx: 1,
    ry: 0,
    rz: 0,
    ux: 0,
    uy: 1,
    uz: 0,
    width: 12,
    curvature: 0,
    bank: 0,
    grip: 1,
    section: 'default',
  };
}

function catmullRom(
  p0: number,
  p1: number,
  p2: number,
  p3: number,
  t: number
): number {
  const t2 = t * t;
  const t3 = t2 * t;
  return (
    0.5 *
    (2 * p1 +
      (-p0 + p2) * t +
      (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2 +
      (-p0 + 3 * p1 - 3 * p2 + p3) * t3)
  );
}

/**
 * An arc-length parameterised racing circuit.
 *
 * Construct once from control nodes; every method is allocation-free afterwards
 * (aside from `sampleAt` when the caller doesn't supply an output frame).
 */
export class TrackSpline {
  readonly closed: boolean;
  /** Uniform spacing between samples (m). */
  readonly step: number;
  /** Total circuit length (m). */
  readonly length: number;
  /** Number of resampled points. */
  readonly count: number;

  // Resampled data (parallel arrays, index = floor(s / step)).
  private readonly px: Float32Array;
  private readonly py: Float32Array;
  private readonly pz: Float32Array;
  private readonly tanX: Float32Array;
  private readonly tanY: Float32Array;
  private readonly tanZ: Float32Array;
  private readonly rightX: Float32Array;
  private readonly rightY: Float32Array;
  private readonly rightZ: Float32Array;
  private readonly upX: Float32Array;
  private readonly upY: Float32Array;
  private readonly upZ: Float32Array;
  private readonly widths: Float32Array;
  private readonly curvatures: Float32Array;
  private readonly banks: Float32Array;
  private readonly grips: Float32Array;
  private readonly sectionIds: Uint8Array;
  private readonly sectionNames: string[];

  // Coarse XZ bucket grid over sample indices, for hint-less projection.
  private readonly cellSize: number;
  private readonly gridMinX: number;
  private readonly gridMinZ: number;
  private readonly gridCols: number;
  private readonly gridRows: number;
  private readonly grid: Int32Array[];

  constructor(nodes: TrackNode[], options: TrackSplineOptions = {}) {
    const closed = options.closed !== false;
    const step = Math.max(0.25, options.step ?? 2);
    const defaultWidth = options.width ?? 12;
    const maxAutoBank = ((options.maxAutoBank ?? 12) * Math.PI) / 180;
    const autoBankCurvature = options.autoBankCurvature ?? 0.03;

    this.closed = closed;
    this.step = step;

    // ---- 1. Dense interpolation of the control polyline -------------------
    const n = nodes.length;
    if (n < 2) {
      throw new Error('TrackSpline needs at least 2 nodes');
    }
    const at = (i: number): TrackNode => {
      if (closed) return nodes[((i % n) + n) % n]!;
      return nodes[Math.max(0, Math.min(n - 1, i))]!;
    };

    const fine = 24; // interpolation steps per control segment
    const segCount = closed ? n : n - 1;
    const dx: number[] = [];
    const dy: number[] = [];
    const dz: number[] = [];
    const dw: number[] = [];
    const dbank: number[] = [];
    const dgrip: number[] = [];
    const dsection: string[] = [];
    let hasExplicitBank = false;

    for (let i = 0; i < segCount; i++) {
      const p0 = at(i - 1);
      const p1 = at(i);
      const p2 = at(i + 1);
      const p3 = at(i + 2);
      if (p1.bank !== undefined || p2.bank !== undefined)
        hasExplicitBank = true;
      for (let k = 0; k < fine; k++) {
        const t = k / fine;
        dx.push(catmullRom(p0.x, p1.x, p2.x, p3.x, t));
        dy.push(catmullRom(p0.y, p1.y, p2.y, p3.y, t));
        dz.push(catmullRom(p0.z, p1.z, p2.z, p3.z, t));
        const w1 = p1.width ?? defaultWidth;
        const w2 = p2.width ?? defaultWidth;
        dw.push(w1 + (w2 - w1) * t);
        const b1 = ((p1.bank ?? 0) * Math.PI) / 180;
        const b2 = ((p2.bank ?? 0) * Math.PI) / 180;
        dbank.push(b1 + (b2 - b1) * t);
        const g1 = p1.grip ?? 1;
        const g2 = p2.grip ?? 1;
        dgrip.push(g1 + (g2 - g1) * t);
        dsection.push(
          t < 0.5 ? (p1.section ?? 'default') : (p2.section ?? 'default')
        );
      }
    }
    if (!closed) {
      const last = nodes[n - 1]!;
      dx.push(last.x);
      dy.push(last.y);
      dz.push(last.z);
      dw.push(last.width ?? defaultWidth);
      dbank.push(((last.bank ?? 0) * Math.PI) / 180);
      dgrip.push(last.grip ?? 1);
      dsection.push(last.section ?? 'default');
    }

    // ---- 2. Cumulative arc length + uniform resample ----------------------
    const dense = dx.length;
    const cum = new Float64Array(dense + 1);
    for (let i = 1; i < dense; i++) {
      cum[i] =
        cum[i - 1]! +
        Math.hypot(
          dx[i]! - dx[i - 1]!,
          dy[i]! - dy[i - 1]!,
          dz[i]! - dz[i - 1]!
        );
    }
    // Closing segment (or the trailing duplicate for open tracks).
    cum[dense] = closed
      ? cum[dense - 1]! +
        Math.hypot(
          dx[0]! - dx[dense - 1]!,
          dy[0]! - dy[dense - 1]!,
          dz[0]! - dz[dense - 1]!
        )
      : cum[dense - 1]!;

    const total = cum[dense]!;
    const count = Math.max(4, Math.round(total / step));
    // Snap the step so `count * step` lands exactly on the total length; a
    // closed circuit must wrap without a seam of leftover metres.
    const realStep = total / count;
    this.length = total;
    this.count = count;
    (this as { step: number }).step = realStep;

    this.px = new Float32Array(count);
    this.py = new Float32Array(count);
    this.pz = new Float32Array(count);
    this.tanX = new Float32Array(count);
    this.tanY = new Float32Array(count);
    this.tanZ = new Float32Array(count);
    this.rightX = new Float32Array(count);
    this.rightY = new Float32Array(count);
    this.rightZ = new Float32Array(count);
    this.upX = new Float32Array(count);
    this.upY = new Float32Array(count);
    this.upZ = new Float32Array(count);
    this.widths = new Float32Array(count);
    this.curvatures = new Float32Array(count);
    this.banks = new Float32Array(count);
    this.grips = new Float32Array(count);
    this.sectionIds = new Uint8Array(count);
    this.sectionNames = [];

    const sectionId = (name: string): number => {
      let idx = this.sectionNames.indexOf(name);
      if (idx < 0) {
        if (this.sectionNames.length >= 255) return 0;
        this.sectionNames.push(name);
        idx = this.sectionNames.length - 1;
      }
      return idx;
    };

    let cursor = 0;
    for (let i = 0; i < count; i++) {
      const target = i * realStep;
      while (cursor < dense - 1 && cum[cursor + 1]! < target) cursor++;
      const segStart = cum[cursor]!;
      const segEnd = cum[cursor + 1]!;
      const segLen = segEnd - segStart;
      const t = segLen > 1e-6 ? (target - segStart) / segLen : 0;
      const j = cursor;
      const k = closed ? (cursor + 1) % dense : Math.min(cursor + 1, dense - 1);
      this.px[i] = dx[j]! + (dx[k]! - dx[j]!) * t;
      this.py[i] = dy[j]! + (dy[k]! - dy[j]!) * t;
      this.pz[i] = dz[j]! + (dz[k]! - dz[j]!) * t;
      this.widths[i] = dw[j]! + (dw[k]! - dw[j]!) * t;
      this.banks[i] = dbank[j]! + (dbank[k]! - dbank[j]!) * t;
      this.grips[i] = dgrip[j]! + (dgrip[k]! - dgrip[j]!) * t;
      this.sectionIds[i] = sectionId(dsection[t < 0.5 ? j : k] ?? 'default');
    }
    if (this.sectionNames.length === 0) this.sectionNames.push('default');

    // ---- 3. Tangents, curvature, frames -----------------------------------
    for (let i = 0; i < count; i++) {
      const a = this.wrapIndex(i - 1);
      const b = this.wrapIndex(i + 1);
      let tx = this.px[b]! - this.px[a]!;
      let ty = this.py[b]! - this.py[a]!;
      let tz = this.pz[b]! - this.pz[a]!;
      const tl = Math.hypot(tx, ty, tz) || 1;
      tx /= tl;
      ty /= tl;
      tz /= tl;
      this.tanX[i] = tx;
      this.tanY[i] = ty;
      this.tanZ[i] = tz;
    }
    for (let i = 0; i < count; i++) {
      // Signed curvature in the horizontal plane: how much the heading turns
      // per metre. Positive = turning left (counter-clockwise seen from above).
      const a = this.wrapIndex(i - 1);
      const b = this.wrapIndex(i + 1);
      const h0 = Math.atan2(this.tanX[a]!, this.tanZ[a]!);
      const h1 = Math.atan2(this.tanX[b]!, this.tanZ[b]!);
      let dh = h1 - h0;
      while (dh > Math.PI) dh -= TWO_PI;
      while (dh < -Math.PI) dh += TWO_PI;
      this.curvatures[i] = -dh / (2 * realStep);
    }
    // Smooth curvature a little: raw finite differences are noisy and both the
    // AI's corner speed and the auto-banking read it directly.
    const smoothed = new Float32Array(count);
    const radius = Math.max(1, Math.round(6 / realStep));
    for (let i = 0; i < count; i++) {
      let sum = 0;
      let weight = 0;
      for (let k = -radius; k <= radius; k++) {
        const idx = this.wrapIndex(i + k);
        if (idx < 0) continue;
        const w = 1 - Math.abs(k) / (radius + 1);
        sum += this.curvatures[idx]! * w;
        weight += w;
      }
      smoothed[i] = weight > 0 ? sum / weight : 0;
    }
    this.curvatures.set(smoothed);

    if (!hasExplicitBank && maxAutoBank > 0) {
      for (let i = 0; i < count; i++) {
        const c = this.curvatures[i]!;
        const t = Math.max(-1, Math.min(1, c / autoBankCurvature));
        // Turning left (c > 0) → positive bank, which raises the right-hand
        // edge and tilts the surface normal into the corner.
        this.banks[i] = t * maxAutoBank;
      }
    }

    for (let i = 0; i < count; i++) {
      const tx = this.tanX[i]!;
      const ty = this.tanY[i]!;
      const tz = this.tanZ[i]!;
      // right = normalize(tangent × worldUp) — points to the driver's right.
      let rx = tz;
      let ry = 0;
      let rz = -tx;
      const rl = Math.hypot(rx, rz) || 1;
      rx /= rl;
      rz /= rl;
      // up = tangent × right (right-handed, y-up). The other order points the
      // surface normal into the ground, which flips every car upside down.
      let ux = ty * rz - tz * ry;
      let uy = tz * rx - tx * rz;
      let uz = tx * ry - ty * rx;
      const ul = Math.hypot(ux, uy, uz) || 1;
      ux /= ul;
      uy /= ul;
      uz /= ul;
      // Rotate the (right, up) pair around the tangent by the bank angle.
      const bank = this.banks[i]!;
      const cb = Math.cos(bank);
      const sb = Math.sin(bank);
      this.rightX[i] = rx * cb + ux * sb;
      this.rightY[i] = ry * cb + uy * sb;
      this.rightZ[i] = rz * cb + uz * sb;
      this.upX[i] = ux * cb - rx * sb;
      this.upY[i] = uy * cb - ry * sb;
      this.upZ[i] = uz * cb - rz * sb;
    }

    // ---- 4. Coarse XZ bucket grid (hint-less projection) -------------------
    let minX = Infinity;
    let maxX = -Infinity;
    let minZ = Infinity;
    let maxZ = -Infinity;
    for (let i = 0; i < count; i++) {
      minX = Math.min(minX, this.px[i]!);
      maxX = Math.max(maxX, this.px[i]!);
      minZ = Math.min(minZ, this.pz[i]!);
      maxZ = Math.max(maxZ, this.pz[i]!);
    }
    this.cellSize = Math.max(20, realStep * 8);
    this.gridMinX = minX - this.cellSize;
    this.gridMinZ = minZ - this.cellSize;
    this.gridCols = Math.max(1, Math.ceil((maxX - minX) / this.cellSize) + 3);
    this.gridRows = Math.max(1, Math.ceil((maxZ - minZ) / this.cellSize) + 3);
    const buckets: number[][] = new Array(this.gridCols * this.gridRows);
    for (let i = 0; i < count; i++) {
      const cx = Math.floor((this.px[i]! - this.gridMinX) / this.cellSize);
      const cz = Math.floor((this.pz[i]! - this.gridMinZ) / this.cellSize);
      const key = cz * this.gridCols + cx;
      (buckets[key] ??= []).push(i);
    }
    this.grid = new Array(this.gridCols * this.gridRows);
    for (let i = 0; i < this.grid.length; i++) {
      this.grid[i] = Int32Array.from(buckets[i] ?? []);
    }
  }

  /** Section names in the order they were first seen. */
  get sections(): readonly string[] {
    return this.sectionNames;
  }

  /** Wrap (closed) or clamp (open) a sample index. */
  private wrapIndex(i: number): number {
    if (this.closed) return ((i % this.count) + this.count) % this.count;
    return Math.max(0, Math.min(this.count - 1, i));
  }

  /** Normalise an arc position into [0, length) for closed circuits. */
  wrapS(s: number): number {
    if (!this.closed) return Math.max(0, Math.min(this.length, s));
    const l = this.length;
    return ((s % l) + l) % l;
  }

  /**
   * Shortest signed difference `a - b` along the circuit, in metres.
   * On a closed track the result is in (-length/2, +length/2].
   */
  deltaS(a: number, b: number): number {
    let d = a - b;
    if (!this.closed) return d;
    const half = this.length / 2;
    while (d > half) d -= this.length;
    while (d < -half) d += this.length;
    return d;
  }

  /** Sample the track frame at arc position `s` (O(1)). */
  sampleAt(s: number, out: TrackFrame = createFrame()): TrackFrame {
    const ws = this.wrapS(s);
    const f = ws / this.step;
    const i0 = this.wrapIndex(Math.floor(f));
    const i1 = this.wrapIndex(i0 + 1);
    const t = f - Math.floor(f);
    const lerp = (a: Float32Array): number => a[i0]! + (a[i1]! - a[i0]!) * t;

    out.s = ws;
    out.x = lerp(this.px);
    out.y = lerp(this.py);
    out.z = lerp(this.pz);
    out.tx = lerp(this.tanX);
    out.ty = lerp(this.tanY);
    out.tz = lerp(this.tanZ);
    const tl = Math.hypot(out.tx, out.ty, out.tz) || 1;
    out.tx /= tl;
    out.ty /= tl;
    out.tz /= tl;
    out.rx = lerp(this.rightX);
    out.ry = lerp(this.rightY);
    out.rz = lerp(this.rightZ);
    const rl = Math.hypot(out.rx, out.ry, out.rz) || 1;
    out.rx /= rl;
    out.ry /= rl;
    out.rz /= rl;
    out.ux = lerp(this.upX);
    out.uy = lerp(this.upY);
    out.uz = lerp(this.upZ);
    const ul = Math.hypot(out.ux, out.uy, out.uz) || 1;
    out.ux /= ul;
    out.uy /= ul;
    out.uz /= ul;
    out.width = lerp(this.widths);
    out.curvature = lerp(this.curvatures);
    out.bank = lerp(this.banks);
    out.grip = lerp(this.grips);
    out.section = this.sectionNames[this.sectionIds[i0]!] ?? 'default';
    return out;
  }

  /** Curvature (1/m, positive = left) at arc position `s`. */
  curvatureAt(s: number): number {
    const f = this.wrapS(s) / this.step;
    const i0 = this.wrapIndex(Math.floor(f));
    const i1 = this.wrapIndex(i0 + 1);
    const t = f - Math.floor(f);
    return (
      this.curvatures[i0]! + (this.curvatures[i1]! - this.curvatures[i0]!) * t
    );
  }

  /**
   * Worst (largest magnitude) curvature over the next `distance` metres from
   * `s`. The AI brakes for the tightest part of the corner it can see, not for
   * the curvature under its own wheels.
   */
  maxCurvatureAhead(s: number, distance: number): number {
    const steps = Math.max(1, Math.ceil(distance / this.step));
    let worst = 0;
    for (let k = 0; k <= steps; k++) {
      const c = this.curvatureAt(s + k * this.step);
      if (Math.abs(c) > Math.abs(worst)) worst = c;
    }
    return worst;
  }

  /** World position of a point at arc `s`, offset `lateral` metres to the right. */
  positionAt(
    s: number,
    lateral: number,
    height = 0,
    out = { x: 0, y: 0, z: 0 }
  ): { x: number; y: number; z: number } {
    const f = this.sampleAt(s, _scratchFrame);
    out.x = f.x + f.rx * lateral + f.ux * height;
    out.y = f.y + f.ry * lateral + f.uy * height;
    out.z = f.z + f.rz * lateral + f.uz * height;
    return out;
  }

  /**
   * Project a world point onto the track.
   *
   * `hintS` is the caller's previous arc position; when supplied the search is
   * limited to a window around it, which both makes it cheap and — critically —
   * keeps a car on its own branch where the circuit crosses over itself.
   * Pass `hintS = null` for a cold global lookup.
   */
  project(
    x: number,
    y: number,
    z: number,
    hintS: number | null = null,
    windowMetres = 60,
    out: TrackProjection = { s: 0, lateral: 0, height: 0, distance: 0 }
  ): TrackProjection {
    let bestIdx = 0;
    let bestDist = Infinity;

    if (hintS !== null && Number.isFinite(hintS)) {
      const centre = Math.round(this.wrapS(hintS) / this.step);
      const span = Math.max(2, Math.ceil(windowMetres / this.step));
      for (let k = -span; k <= span; k++) {
        const i = this.wrapIndex(centre + k);
        const d =
          (x - this.px[i]!) ** 2 +
          (y - this.py[i]!) ** 2 +
          (z - this.pz[i]!) ** 2;
        if (d < bestDist) {
          bestDist = d;
          bestIdx = i;
        }
      }
    } else {
      // Cold path: bucket lookup around the query cell, then a full scan if the
      // point is off the grid entirely.
      const cx = Math.floor((x - this.gridMinX) / this.cellSize);
      const cz = Math.floor((z - this.gridMinZ) / this.cellSize);
      let considered = 0;
      for (let oz = -1; oz <= 1; oz++) {
        for (let ox = -1; ox <= 1; ox++) {
          const gx = cx + ox;
          const gz = cz + oz;
          if (gx < 0 || gz < 0 || gx >= this.gridCols || gz >= this.gridRows)
            continue;
          const bucket = this.grid[gz * this.gridCols + gx];
          if (!bucket) continue;
          for (let b = 0; b < bucket.length; b++) {
            const i = bucket[b]!;
            considered++;
            const d =
              (x - this.px[i]!) ** 2 +
              (y - this.py[i]!) ** 2 +
              (z - this.pz[i]!) ** 2;
            if (d < bestDist) {
              bestDist = d;
              bestIdx = i;
            }
          }
        }
      }
      if (considered === 0) {
        for (let i = 0; i < this.count; i++) {
          const d =
            (x - this.px[i]!) ** 2 +
            (y - this.py[i]!) ** 2 +
            (z - this.pz[i]!) ** 2;
          if (d < bestDist) {
            bestDist = d;
            bestIdx = i;
          }
        }
      }
    }

    // Refine within the two segments touching the best sample, so the arc
    // position is continuous rather than quantised to `step`.
    const refine = (i0: number, i1: number): { s: number; d: number } => {
      const ax = this.px[i0]!;
      const ay = this.py[i0]!;
      const az = this.pz[i0]!;
      const bx = this.px[i1]!;
      const by = this.py[i1]!;
      const bz = this.pz[i1]!;
      const ex = bx - ax;
      const ey = by - ay;
      const ez = bz - az;
      const len2 = ex * ex + ey * ey + ez * ez;
      let t =
        len2 > 1e-9
          ? ((x - ax) * ex + (y - ay) * ey + (z - az) * ez) / len2
          : 0;
      t = Math.max(0, Math.min(1, t));
      const qx = ax + ex * t;
      const qy = ay + ey * t;
      const qz = az + ez * t;
      const d = (x - qx) ** 2 + (y - qy) ** 2 + (z - qz) ** 2;
      return { s: (i0 + t) * this.step, d };
    };

    const prev = this.wrapIndex(bestIdx - 1);
    const next = this.wrapIndex(bestIdx + 1);
    const a = refine(prev, bestIdx);
    const b = refine(bestIdx, next);
    // On a closed track the `prev` segment may straddle the seam; refine()
    // returns its start index times step, which is still the correct arc.
    const pick = a.d <= b.d ? a : b;

    const s = this.wrapS(pick.s);
    const f = this.sampleAt(s, _scratchFrame);
    const dxp = x - f.x;
    const dyp = y - f.y;
    const dzp = z - f.z;
    out.s = s;
    out.lateral = dxp * f.rx + dyp * f.ry + dzp * f.rz;
    out.height = dxp * f.ux + dyp * f.uy + dzp * f.uz;
    out.distance = Math.sqrt(pick.d);
    return out;
  }

  /** Iterate samples: `fn(index, x, y, z)` — used by mesh/minimap builders. */
  forEachSample(
    fn: (i: number, x: number, y: number, z: number) => void
  ): void {
    for (let i = 0; i < this.count; i++) {
      fn(i, this.px[i]!, this.py[i]!, this.pz[i]!);
    }
  }

  /**
   * Two stretches of the circuit whose **road corridors** overlap.
   *
   * Centerlines that never cross can still collide once the road has width:
   * two arms 12 m apart on a 16 m wide track share 4 m of tarmac. That shows up
   * as z-fighting ribbons, barriers standing in the middle of the road, and —
   * worst — a car whose projection can flip between the two arms.
   *
   * `verticalClearance` is the height difference above which an overlap is
   * intentional (a flyover) rather than a mistake.
   */
  selfOverlaps(
    verticalClearance = 5,
    minArcSeparation = 40
  ): { aS: number; bS: number; gap: number; height: number }[] {
    const hits: { aS: number; bS: number; gap: number; height: number }[] = [];
    const stride = Math.max(1, Math.round(4 / this.step));
    for (let i = 0; i < this.count; i += stride) {
      const halfA = this.widths[i]! * 0.5;
      for (let j = i + stride; j < this.count; j += stride) {
        // Ignore neighbours along the track — those *should* touch.
        const arcGap = Math.abs(this.deltaS(i * this.step, j * this.step));
        if (arcGap < minArcSeparation) continue;
        const dx = this.px[i]! - this.px[j]!;
        const dz = this.pz[i]! - this.pz[j]!;
        const planDistance = Math.hypot(dx, dz);
        const needed = halfA + this.widths[j]! * 0.5;
        if (planDistance >= needed) continue;
        const dy = Math.abs(this.py[i]! - this.py[j]!);
        if (dy >= verticalClearance) continue; // a genuine flyover
        hits.push({
          aS: i * this.step,
          bS: j * this.step,
          gap: planDistance,
          height: dy,
        });
      }
    }
    return hits;
  }

  /** Axis-aligned XZ bounds of the centerline — used to frame the minimap. */
  bounds(): { minX: number; maxX: number; minZ: number; maxZ: number } {
    let minX = Infinity;
    let maxX = -Infinity;
    let minZ = Infinity;
    let maxZ = -Infinity;
    for (let i = 0; i < this.count; i++) {
      minX = Math.min(minX, this.px[i]!);
      maxX = Math.max(maxX, this.px[i]!);
      minZ = Math.min(minZ, this.pz[i]!);
      maxZ = Math.max(maxZ, this.pz[i]!);
    }
    return { minX, maxX, minZ, maxZ };
  }
}

/** Shared scratch frame for the class's internal sampling. */
const _scratchFrame: TrackFrame = createFrame();

/**
 * Parse `<RaceTrack nodes="x y z [width] ...">`-style flat number lists into
 * nodes. Accepts 3 numbers per node (x y z) or 4 (x y z width).
 */
export function nodesFromFlatList(
  values: number[],
  stride: 3 | 4 = 3,
  sections?: string[]
): TrackNode[] {
  const nodes: TrackNode[] = [];
  const count = Math.floor(values.length / stride);
  for (let i = 0; i < count; i++) {
    const node: TrackNode = {
      x: values[i * stride]!,
      y: values[i * stride + 1]!,
      z: values[i * stride + 2]!,
    };
    if (stride === 4) node.width = values[i * stride + 3]!;
    if (sections && sections[i]) node.section = sections[i]!;
    nodes.push(node);
  }
  return nodes;
}
