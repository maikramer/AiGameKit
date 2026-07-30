import * as THREE from 'three';
import { distanceToPolyline } from './geometry';

/**
 * Road-network fusion:
 * - **End-to-end** (city gate ↔ desert artery): stitch into ONE continuous
 *   ribbon with width lerp — no circular disc stamps.
 * - **T-junction**: spur docks onto through traffic (solid tip + short extend).
 *
 * Pure / deterministic — unit-testable without GPU.
 */

/** Ends within this distance (m) snap into one junction node. */
export const ROAD_JUNCTION_END_SNAP = 2.5;
/** End sits on another road's carriageway within halfWidth + slack. */
export const ROAD_JUNCTION_SIDE_SLACK = 1.5;
/** Cluster candidate arms closer than this into one node. */
export const ROAD_JUNCTION_CLUSTER = 3.0;

export interface RoadJunctionInput {
  eid: number;
  path: number[];
  width: number;
  /** Per-vertex widths (m); omit → every vertex uses `width`. */
  widths?: number[];
  edgeFeather: number;
  textureUrl: string | null;
  normalMapUrl: string | null;
  textureScale: number;
}

export interface RoadJunctionArm {
  eid: number;
  /** `through` = T onto this road's mid-span (no retract on that ribbon). */
  end: 'start' | 'end' | 'through';
}

export interface RoadJunction {
  /** Stable key from quantized world XZ. */
  id: string;
  x: number;
  z: number;
  /** Opaque core radius (m) — covers the widest half-width. */
  radius: number;
  /** Outer alpha fade beyond `radius` (m). */
  feather: number;
  maxWidth: number;
  textureUrl: string | null;
  normalMapUrl: string | null;
  textureScale: number;
  arms: RoadJunctionArm[];
}

/** Per-road docking plan produced from the junction graph. */
export interface RoadFusionPlan {
  startRetract: number;
  endRetract: number;
  /** Legacy wedge fill when no fusion disc (rare). */
  startExtend: number;
  endExtend: number;
  startSolid: boolean;
  endSolid: boolean;
  /** Target width (m) at the start/end for taper into the junction. */
  startWidth: number;
  endWidth: number;
  /** Arc length over which width lerps to the junction max (m). */
  blendLen: number;
}

export function emptyFusionPlan(width: number): RoadFusionPlan {
  return {
    startRetract: 0,
    endRetract: 0,
    startExtend: 0,
    endExtend: 0,
    startSolid: false,
    endSolid: false,
    startWidth: width,
    endWidth: width,
    blendLen: 0,
  };
}

function endPoint(
  path: number[],
  end: 'start' | 'end'
): { x: number; z: number } {
  if (end === 'start') return { x: path[0]!, z: path[1]! };
  const n = path.length;
  return { x: path[n - 2]!, z: path[n - 1]! };
}

function junctionId(x: number, z: number): string {
  return `${Math.round(x * 2) / 2}_${Math.round(z * 2) / 2}`;
}

/**
 * Build the junction graph from all authored roads. An end that sits on another
 * road's endpoint (end-to-end) or carriageway (T) becomes an arm; nearby arms
 * cluster into one fusion node sized for the widest participant.
 */
export function detectRoadJunctions(
  roads: RoadJunctionInput[]
): RoadJunction[] {
  type Cand = {
    x: number;
    z: number;
    width: number;
    feather: number;
    textureUrl: string | null;
    normalMapUrl: string | null;
    textureScale: number;
    arm: RoadJunctionArm;
  };
  const cands: Cand[] = [];

  for (const r of roads) {
    if (r.path.length < 4) continue;
    for (const end of ['start', 'end'] as const) {
      const p = endPoint(r.path, end);
      let touches = false;
      let maxW = r.width;
      let maxFeather = r.edgeFeather;
      let tex = r.textureUrl;
      let nrm = r.normalMapUrl;
      let texScale = r.textureScale;
      /** Roads this tip meets on the mid-span (T-junction through traffic). */
      const through: RoadJunctionInput[] = [];
      for (const o of roads) {
        if (o.eid === r.eid || o.path.length < 4) continue;
        const oStart = endPoint(o.path, 'start');
        const oEnd = endPoint(o.path, 'end');
        const endDist = Math.min(
          Math.hypot(p.x - oStart.x, p.z - oStart.z),
          Math.hypot(p.x - oEnd.x, p.z - oEnd.z)
        );
        const sideDist = distanceToPolyline(o.path, p.x, p.z);
        const onEnd = endDist <= ROAD_JUNCTION_END_SNAP;
        const onSide = sideDist <= o.width / 2 + ROAD_JUNCTION_SIDE_SLACK;
        if (!onEnd && !onSide) continue;
        touches = true;
        // Mid-span touch only → through arm on the neighbour (no tip there).
        if (onSide && !onEnd) through.push(o);
        if (o.width > maxW) {
          maxW = o.width;
          // Prefer textures from the wider carriageway at the join.
          if (o.textureUrl) {
            tex = o.textureUrl;
            nrm = o.normalMapUrl;
            texScale = o.textureScale;
          }
        }
        maxFeather = Math.max(maxFeather, o.edgeFeather);
      }
      if (!touches) continue;
      cands.push({
        x: p.x,
        z: p.z,
        width: maxW,
        feather: maxFeather,
        textureUrl: tex,
        normalMapUrl: nrm,
        textureScale: texScale,
        arm: { eid: r.eid, end },
      });
      for (const o of through) {
        cands.push({
          x: p.x,
          z: p.z,
          width: Math.max(maxW, o.width),
          feather: Math.max(maxFeather, o.edgeFeather),
          textureUrl: tex ?? o.textureUrl,
          normalMapUrl: nrm ?? o.normalMapUrl,
          textureScale: texScale,
          arm: { eid: o.eid, end: 'through' },
        });
      }
    }
  }

  // Greedy cluster by proximity.
  const used = new Uint8Array(cands.length);
  const out: RoadJunction[] = [];
  for (let i = 0; i < cands.length; i++) {
    if (used[i]) continue;
    const seed = cands[i]!;
    const group = [seed];
    used[i] = 1;
    for (let j = i + 1; j < cands.length; j++) {
      if (used[j]) continue;
      const c = cands[j]!;
      if (Math.hypot(c.x - seed.x, c.z - seed.z) > ROAD_JUNCTION_CLUSTER)
        continue;
      group.push(c);
      used[j] = 1;
    }
    let sx = 0;
    let sz = 0;
    let maxW = 0;
    let maxFeather = 0;
    let tex: string | null = null;
    let nrm: string | null = null;
    let texScale = 16;
    const arms: RoadJunctionArm[] = [];
    const seenArm = new Set<string>();
    for (const g of group) {
      sx += g.x;
      sz += g.z;
      if (g.width > maxW) {
        maxW = g.width;
        tex = g.textureUrl;
        nrm = g.normalMapUrl;
        texScale = g.textureScale;
      }
      maxFeather = Math.max(maxFeather, g.feather);
      const key = `${g.arm.eid}:${g.arm.end}`;
      if (!seenArm.has(key)) {
        seenArm.add(key);
        arms.push(g.arm);
      }
    }
    // Need ≥2 distinct roads (end-to-end, T with through, or a loop).
    const roadIds = new Set(arms.map((a) => a.eid));
    if (roadIds.size < 2) continue;
    const x = sx / group.length;
    const z = sz / group.length;
    const half = maxW / 2;
    const feather = Math.max(0.55, Math.min(Math.max(maxFeather, 0.55), 1.4));
    out.push({
      id: junctionId(x, z),
      x,
      z,
      // Oversized core: must cover the outer corner wedge where two angled
      // ribbons meet (city 58,4→62,1 vs desert 62,1→70,5).
      radius: half + 0.85,
      feather,
      maxWidth: maxW,
      textureUrl: tex,
      normalMapUrl: nrm,
      textureScale: Math.max(texScale, 0.01),
      arms,
    });
  }
  return out;
}

/**
 * T-junction docking only. End-to-end joins are handled by
 * {@link stitchEndToEndChains} (one ribbon) — never by discs or tip extend.
 */
/** Flare factor at multi-arm crossings (matches network expand). */
export const ROAD_JUNCTION_TIP_FLARE = 1.45;

export function planRoadFusion(
  roads: RoadJunctionInput[],
  junctions: RoadJunction[]
): Map<number, RoadFusionPlan> {
  const plans = new Map<number, RoadFusionPlan>();
  for (const r of roads) {
    plans.set(r.eid, emptyFusionPlan(r.width));
  }
  for (const j of junctions) {
    const hasThrough = j.arms.some((a) => a.end === 'through');
    const isCross = j.arms.filter((a) => a.end !== 'through').length >= 3;
    // T-junctions (through) and pure crosses (X/Y) both get tip flare + solid dock.
    if (!hasThrough && !isCross) continue;
    const flareW = j.maxWidth * (isCross ? ROAD_JUNCTION_TIP_FLARE : 1);
    const extend = hasThrough ? Math.max(0.6, j.maxWidth * 0.35) : 0;
    const blend = Math.max(flareW, 4);
    for (const arm of j.arms) {
      const plan = plans.get(arm.eid);
      if (!plan || arm.end === 'through') continue;
      if (arm.end === 'start') {
        plan.startExtend = Math.max(plan.startExtend, extend);
        plan.startSolid = true;
        plan.startWidth = Math.max(plan.startWidth, flareW);
        plan.blendLen = Math.max(plan.blendLen, blend);
      } else {
        plan.endExtend = Math.max(plan.endExtend, extend);
        plan.endSolid = true;
        plan.endWidth = Math.max(plan.endWidth, flareW);
        plan.blendLen = Math.max(plan.blendLen, blend);
      }
    }
  }
  return plans;
}

export interface StitchedRoadChain {
  /** Owns the painted ribbon for the whole chain. */
  leaderEid: number;
  memberEids: number[];
  path: number[];
  /** Per-vertex width (same count as path points). */
  widths: number[];
  textureUrl: string | null;
  normalMapUrl: string | null;
  textureScale: number;
  edgeFeather: number;
}

function texturesCompatible(
  a: RoadJunctionInput,
  b: RoadJunctionInput
): boolean {
  if (!a.textureUrl || !b.textureUrl) return true;
  return a.textureUrl === b.textureUrl;
}

/** Reverse a flat `[x,z,...]` polyline. */
export function reverseRoadPath(path: number[]): number[] {
  const out: number[] = [];
  for (let i = path.length - 2; i >= 0; i -= 2) {
    out.push(path[i]!, path[i + 1]!);
  }
  return out;
}

/**
 * Walk end-to-end tip links into chains and concatenate polylines. Leader =
 * lowest eid (stable). Absorbed members must not paint their own ribbon.
 */
export function stitchEndToEndChains(
  roads: RoadJunctionInput[],
  junctions: RoadJunction[]
): StitchedRoadChain[] {
  const byEid = new Map(roads.map((r) => [r.eid, r]));
  // tip key "eid:start" | "eid:end" → neighbour tip key
  const link = new Map<string, string>();

  for (const j of junctions) {
    const tips = j.arms.filter((a) => a.end === 'start' || a.end === 'end');
    if (tips.length !== 2) continue;
    const a = tips[0]!;
    const b = tips[1]!;
    if (a.eid === b.eid) continue;
    const ra = byEid.get(a.eid);
    const rb = byEid.get(b.eid);
    if (!ra || !rb || !texturesCompatible(ra, rb)) continue;
    const ka = `${a.eid}:${a.end}`;
    const kb = `${b.eid}:${b.end}`;
    link.set(ka, kb);
    link.set(kb, ka);
  }
  if (link.size === 0) return [];

  const degree = (eid: number): number => {
    let d = 0;
    if (link.has(`${eid}:start`)) d++;
    if (link.has(`${eid}:end`)) d++;
    return d;
  };

  const visited = new Set<number>();
  const chains: StitchedRoadChain[] = [];

  const starts = roads
    .map((r) => r.eid)
    .filter((eid) => degree(eid) === 1)
    .sort((a, b) => a - b);

  const walkFrom = (startEid: number, startEnd: 'start' | 'end'): void => {
    if (visited.has(startEid)) return;
    type Step = { eid: number; enterEnd: 'start' | 'end' | null };
    const steps: Step[] = [{ eid: startEid, enterEnd: null }];
    let curEid = startEid;
    let leaveEnd = startEnd;
    visited.add(startEid);

    for (;;) {
      const nextKey = link.get(`${curEid}:${leaveEnd}`);
      if (!nextKey) break;
      const [ns, ne] = nextKey.split(':') as [string, 'start' | 'end'];
      const nextEid = Number(ns);
      if (visited.has(nextEid)) break;
      visited.add(nextEid);
      steps.push({ eid: nextEid, enterEnd: ne });
      // Leave through the opposite tip.
      leaveEnd = ne === 'start' ? 'end' : 'start';
      curEid = nextEid;
      if (!link.has(`${curEid}:${leaveEnd}`)) break;
    }

    if (steps.length < 2) return;

    const path: number[] = [];
    const widths: number[] = [];
    let textureUrl: string | null = null;
    let normalMapUrl: string | null = null;
    let textureScale = 16;
    let edgeFeather = 1.1;
    let maxW = 0;

    for (let s = 0; s < steps.length; s++) {
      const step = steps[s]!;
      const road = byEid.get(step.eid)!;
      let seg = road.path.slice();
      let segWidths =
        road.widths && road.widths.length === road.path.length / 2
          ? road.widths.slice()
          : null;
      // Orient: first road leaves via startEnd; later roads enter via enterEnd
      // so we reverse when the enter tip is the path's end (want enter at front).
      let reversed = false;
      if (s === 0) {
        if (startEnd === 'start') reversed = true;
      } else if (step.enterEnd === 'end') {
        reversed = true;
      }
      if (reversed) {
        seg = reverseRoadPath(seg);
        if (segWidths) segWidths.reverse();
      }
      // Skip duplicate join vertex.
      let i0 = 0;
      if (path.length >= 2) {
        const lx = path[path.length - 2]!;
        const lz = path[path.length - 1]!;
        if (Math.hypot(seg[0]! - lx, seg[1]! - lz) <= ROAD_JUNCTION_END_SNAP) {
          i0 = 2;
        }
      }
      for (let i = i0; i < seg.length; i += 2) {
        path.push(seg[i]!, seg[i + 1]!);
        const vi = i / 2;
        widths.push(segWidths?.[vi] ?? road.width);
      }
      const roadMax = segWidths
        ? segWidths.reduce((a, b) => Math.max(a, b), 0)
        : road.width;
      if (roadMax > maxW) {
        maxW = roadMax;
        textureUrl = road.textureUrl;
        normalMapUrl = road.normalMapUrl;
        textureScale = road.textureScale;
        edgeFeather = road.edgeFeather;
      }
    }

    if (path.length < 4) return;
    const memberEids = steps.map((st) => st.eid);
    const leaderEid = Math.min(...memberEids);
    chains.push({
      leaderEid,
      memberEids,
      path,
      widths,
      textureUrl,
      normalMapUrl,
      textureScale,
      edgeFeather,
    });
  };

  for (const eid of starts) {
    if (visited.has(eid)) continue;
    // Begin at the tip that has a link.
    if (link.has(`${eid}:end`)) walkFrom(eid, 'end');
    else if (link.has(`${eid}:start`)) walkFrom(eid, 'start');
  }

  // Loops / leftover (degree 2 everywhere): pick lowest eid.
  for (const r of roads) {
    if (visited.has(r.eid) || degree(r.eid) === 0) continue;
    if (link.has(`${r.eid}:end`)) walkFrom(r.eid, 'end');
    else if (link.has(`${r.eid}:start`)) walkFrom(r.eid, 'start');
  }

  return chains;
}

/**
 * Smooth width along a stitched path: hard steps at joins become a lerp over
 * `blendMeters` so the carriageway breathes instead of stepping.
 */
export function makeWidthAtFromVertexWidths(
  path: number[],
  widths: number[],
  blendMeters = 8
): (arc: number, totalLen: number) => number {
  const n = path.length / 2;
  const arcs: number[] = [0];
  let total = 0;
  for (let i = 0; i < n - 1; i++) {
    total += Math.hypot(
      path[(i + 1) * 2]! - path[i * 2]!,
      path[(i + 1) * 2 + 1]! - path[i * 2 + 1]!
    );
    arcs.push(total);
  }
  const blend = Math.max(blendMeters, 0.01);
  return (arc: number, _totalLen: number) => {
    const a = Math.max(0, Math.min(arc, total));
    // Soften: average widths of verts within blend window, distance-weighted.
    let wSum = 0;
    let kSum = 0;
    for (let i = 0; i < n; i++) {
      const d = Math.abs(arcs[i]! - a);
      if (d > blend) continue;
      const k = 1 - d / blend;
      wSum += widths[i]! * k;
      kSum += k;
    }
    if (kSum <= 0) {
      // Fallback: nearest vertex.
      let best = 0;
      let bestD = Infinity;
      for (let i = 0; i < n; i++) {
        const d = Math.abs(arcs[i]! - a);
        if (d < bestD) {
          bestD = d;
          best = i;
        }
      }
      return widths[best]!;
    }
    return wSum / kSum;
  };
}

/** Chain membership lookup: leader paints, absorbed only carve. */
export function chainRoleFor(
  chains: StitchedRoadChain[],
  eid: number
): { role: 'leader' | 'absorbed' | 'solo'; chain?: StitchedRoadChain } {
  for (const c of chains) {
    if (!c.memberEids.includes(eid)) continue;
    return {
      role: c.leaderEid === eid ? 'leader' : 'absorbed',
      chain: c,
    };
  }
  return { role: 'solo' };
}

/** Trim `amount` metres from the start of a polyline along its arc. */
export function trimPathStart(path: number[], amount: number): number[] {
  if (amount <= 0 || path.length < 4) return path;
  let remain = amount;
  let i = 0;
  while (i + 3 < path.length && remain > 0) {
    const ax = path[i]!;
    const az = path[i + 1]!;
    const bx = path[i + 2]!;
    const bz = path[i + 3]!;
    const len = Math.hypot(bx - ax, bz - az);
    if (len <= remain) {
      remain -= len;
      i += 2;
      continue;
    }
    const t = remain / len;
    const nx = ax + (bx - ax) * t;
    const nz = az + (bz - az) * t;
    return [nx, nz, ...path.slice(i + 2)];
  }
  // Would collapse the path — keep a stub of the last segment.
  if (path.length - i < 4) {
    const n = path.length;
    return path.slice(n - 4);
  }
  return path.slice(i);
}

/** Trim `amount` metres from the end of a polyline along its arc. */
export function trimPathEnd(path: number[], amount: number): number[] {
  if (amount <= 0 || path.length < 4) return path;
  let remain = amount;
  let i = path.length - 2;
  while (i >= 2 && remain > 0) {
    const bx = path[i]!;
    const bz = path[i + 1]!;
    const ax = path[i - 2]!;
    const az = path[i - 1]!;
    const len = Math.hypot(bx - ax, bz - az);
    if (len <= remain) {
      remain -= len;
      i -= 2;
      continue;
    }
    const t = 1 - remain / len;
    const nx = ax + (bx - ax) * t;
    const nz = az + (bz - az) * t;
    return [...path.slice(0, i), nx, nz];
  }
  if (i < 2) return path.slice(0, 4);
  return path.slice(0, i + 2);
}

/**
 * Shorten both ends. Falls back to the original path if trimming would leave
 * fewer than two points.
 */
export function retractPathEnds(
  path: number[],
  startAmount: number,
  endAmount: number
): number[] {
  if (path.length < 4) return path;
  let out = path;
  if (startAmount > 0) out = trimPathStart(out, startAmount);
  if (endAmount > 0) out = trimPathEnd(out, endAmount);
  return out.length >= 4 ? out : path;
}

/**
 * Width along the ribbon: tapers from junction maxWidth at docked ends into the
 * road's authored width over `blendLen`.
 */
export function makeFusionWidthAt(
  roadWidth: number,
  plan: RoadFusionPlan
): ((arc: number, totalLen: number) => number) | undefined {
  if (plan.blendLen <= 0 && !plan.startSolid && !plan.endSolid)
    return undefined;
  const blend = Math.max(plan.blendLen, 0.01);
  return (arc: number, totalLen: number) => {
    let w = roadWidth;
    if (plan.startSolid && plan.startWidth > roadWidth) {
      const t = Math.min(arc / blend, 1);
      const s = t * t * (3 - 2 * t);
      w = Math.max(w, plan.startWidth + (roadWidth - plan.startWidth) * s);
    }
    if (plan.endSolid && plan.endWidth > roadWidth) {
      const t = Math.min(Math.max(totalLen - arc, 0) / blend, 1);
      const s = t * t * (3 - 2 * t);
      w = Math.max(w, plan.endWidth + (roadWidth - plan.endWidth) * s);
    }
    return w;
  };
}

export interface JunctionGeometryOptions {
  radius: number;
  feather: number;
  textureScale: number;
  segments?: number;
  heightAt?: (x: number, z: number) => number;
  yOffset?: number;
  seed?: number;
  /** Extra lift above sampled terrain (m). Default clears LOD sand lids. */
  clearance?: number;
}

/**
 * Max walk/mesh height under a disc — LOD triangles peak above the analytic
 * bed; a plate at the center sample alone sinks under sand at the rim.
 */
export function sampleJunctionPlateY(
  heightAt: (x: number, z: number) => number,
  x: number,
  z: number,
  radius: number
): number {
  let y = heightAt(x, z);
  const rings = [0.35, 0.7, 1];
  const spokes = 10;
  for (const f of rings) {
    const r = radius * f;
    for (let i = 0; i < spokes; i++) {
      const ang = (i / spokes) * Math.PI * 2;
      const h = heightAt(x + Math.cos(ang) * r, z + Math.sin(ang) * r);
      if (h > y) y = h;
    }
  }
  return y;
}

/**
 * Circular fusion patch: opaque disc of `radius` + feather ring to alpha 0.
 * UV in world metres / textureScale so cobble tiles match neighbouring ribbons.
 */
export function makeJunctionGeometry(
  x: number,
  z: number,
  opts: JunctionGeometryOptions
): THREE.BufferGeometry {
  const radius = Math.max(opts.radius, 0.2);
  const feather = Math.max(opts.feather, 0.05);
  const outer = radius + feather;
  const segs = Math.max(12, opts.segments ?? 28);
  const scale = Math.max(opts.textureScale, 0.01);
  const yOffset = opts.yOffset ?? 0;
  const clearance = opts.clearance ?? 0.04;
  const heightAt = opts.heightAt ?? (() => 0);
  const seed = opts.seed ?? x * 11.1 + z * 3.7;
  // Flat plate on the *highest* terrain under the disc — not the center alone.
  const plateY =
    sampleJunctionPlateY(heightAt, x, z, outer) + yOffset + clearance;

  const positions: number[] = [];
  const uvs: number[] = [];
  const colors: number[] = [];
  const indices: number[] = [];

  // Rings: center, core rim, outer feather rim.
  const rings: Array<{ r: number; a: number }> = [
    { r: 0, a: 1 },
    { r: radius, a: 1 },
    { r: outer, a: 0 },
  ];

  for (let ring = 0; ring < rings.length; ring++) {
    const { r, a } = rings[ring]!;
    if (ring === 0) {
      positions.push(x, plateY, z);
      uvs.push(x / scale, z / scale);
      colors.push(1, 1, 1, a);
      continue;
    }
    for (let s = 0; s < segs; s++) {
      const ang = (s / segs) * Math.PI * 2;
      // Mild organic rim so the disc doesn't read as a perfect circle stamp.
      const jitter =
        r > radius
          ? 0
          : 0.04 * Math.sin(ang * 3 + seed) + 0.03 * Math.cos(ang * 5 - seed);
      const rr = r * (1 + jitter);
      const px = x + Math.cos(ang) * rr;
      const pz = z + Math.sin(ang) * rr;
      positions.push(px, plateY, pz);
      uvs.push(px / scale, pz / scale);
      colors.push(1, 1, 1, a);
    }
  }

  // Camera-facing winding (FrontSide cull). Normals are forced +Y below —
  // do not flip winding to satisfy computeVertexNormals (that culls the disc).
  for (let s = 0; s < segs; s++) {
    const a = 1 + s;
    const b = 1 + ((s + 1) % segs);
    indices.push(0, a, b);
  }
  // Core → feather ring.
  const core0 = 1;
  const outer0 = 1 + segs;
  for (let s = 0; s < segs; s++) {
    const a = core0 + s;
    const b = core0 + ((s + 1) % segs);
    const c = outer0 + s;
    const d = outer0 + ((s + 1) % segs);
    indices.push(a, c, b, b, c, d);
  }

  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geo.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2));
  geo.setAttribute('color', new THREE.Float32BufferAttribute(colors, 4));
  geo.setIndex(indices);

  // Flat plate: force heightfield / +Y normals for lighting. Winding above
  // stays camera-facing for FrontSide cull — independent of this attribute.
  const e = 0.35;
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
  const normals = new Float32Array((positions.length / 3) * 3);
  for (let i = 0; i < normals.length; i += 3) {
    normals[i] = nx;
    normals[i + 1] = ny;
    normals[i + 2] = nz;
  }
  geo.setAttribute('normal', new THREE.Float32BufferAttribute(normals, 3));
  return geo;
}

/** Compact signature so systems can skip rebuild when the network is stable. */
export function junctionNetworkSignature(junctions: RoadJunction[]): string {
  return junctions
    .map(
      (j) =>
        `${j.id}:${j.radius.toFixed(2)}:${j.arms
          .map((a) => `${a.eid}${a.end[0]}`)
          .sort()
          .join(',')}`
    )
    .sort()
    .join('|');
}
