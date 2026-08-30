import { flattenNumberList } from '../../core';
import type { ParsedElement } from '../../core';
import {
  expandRoadNetworkToRoads,
  parseFlatXZList,
  parseRoadNetworkElement,
} from '../../plugins/road/network';
import { shoreFraction } from '../../plugins/water/carve';
import { distanceToPath } from '../../plugins/water/path-utils';
import type { AnalyzeIssue } from './types';

/**
 * Offline road-geometry checks over the expanded world tree:
 * - road polylines crossing Lake/River water surfaces without a bridge profile;
 * - bridges that cross no water at all (stale span);
 * - authored `heights` grade above `flatten-max-grade`;
 * - hairpin turns, near-zero segments and per-point list mismatches.
 *
 * Water extents mirror the runtime registry: lakes are discs at the waterline
 * radius (`shoreFraction(depth, waterOffset)·radius`), rivers are channels of
 * half the waterline width around the polyline.
 */

const DEFAULT_MAX_GRADE = 0.22;
const SAMPLE_STEP_M = 1;
/** Shorter dips at the waterline edge are approach noise, not a crossing. */
const MIN_WATER_RUN_M = 1;
/** Turn angle (from straight) above which a node is a hairpin. */
const HAIRPIN_DEG = 45;
const DEGENERATE_M = 0.05;
/** A bridge at least this far past every waterline is crossing nothing. */
const BRIDGE_LAND_REACH_M = 2;
const MAX_ISSUES_PER_ROAD = 8;

interface WaterSpec {
  label: string;
  kind: 'lake' | 'river';
  /** Lake centre XZ (kind lake). */
  x: number;
  z: number;
  /** River centreline flat [x,z,…] (kind river). */
  path: number[];
  /** Waterline reach: lake waterline radius / river half waterline width. */
  shoreHalf: number;
}

interface RoadSpec {
  label: string;
  /** Flat [x,z,…] world polyline. */
  path: number[];
  heights?: number[];
  widths?: number[];
  banks?: number[];
  isBridge: boolean;
  flatten: boolean;
  maxGrade: number;
}

function walk(el: ParsedElement, visit: (e: ParsedElement) => void): void {
  visit(el);
  for (const c of el.children) walk(c, visit);
}

function num(raw: unknown, fallback: number): number {
  const n = typeof raw === 'number' ? raw : parseFloat(String(raw ?? ''));
  return Number.isFinite(n) ? n : fallback;
}

function boolAttr(raw: unknown, fallback: boolean): boolean {
  if (raw === undefined || raw === null || raw === '') return fallback;
  if (raw === 1 || raw === true || raw === '1' || raw === 'true') return true;
  if (raw === 0 || raw === false || raw === '0' || raw === 'false')
    return false;
  return fallback;
}

function collectWaterBodies(root: ParsedElement): WaterSpec[] {
  const out: WaterSpec[] = [];
  walk(root, (el) => {
    const tag = el.tagName?.toLowerCase();
    if (tag === 'lake') {
      const at = parseFlatXZList(el.attributes.at);
      if (at.length < 2) return;
      const radius = num(el.attributes.radius, 6);
      const shoreR =
        shoreFraction(
          num(el.attributes.depth, 1.5),
          num(el.attributes['water-offset'], 0.5)
        ) * radius;
      if (shoreR <= 0) return;
      out.push({
        label: `Lake@(${at[0]},${at[1]})`,
        kind: 'lake',
        x: at[0]!,
        z: at[1]!,
        path: [],
        shoreHalf: shoreR,
      });
    } else if (tag === 'river') {
      const path = parseFlatXZList(el.attributes.path);
      if (path.length < 4) return;
      const width = num(el.attributes.width, 6);
      const shoreW =
        width *
        shoreFraction(
          num(el.attributes.depth, 1.5),
          num(el.attributes['water-offset'], 0.3)
        );
      if (shoreW <= 0) return;
      out.push({
        label: `River@(${path[0]},${path[1]})`,
        kind: 'river',
        x: path[0]!,
        z: path[1]!,
        path,
        shoreHalf: shoreW / 2,
      });
    }
  });
  return out;
}

function roadFromElement(
  el: ParsedElement,
  labelPrefix?: string
): RoadSpec | null {
  const attrs = el.attributes;
  const path = flattenNumberList(attrs.path);
  if (path.length < 4) return null;
  const perPoint = (raw: unknown): number[] | undefined => {
    const list = flattenNumberList(raw);
    return list.length > 0 ? list : undefined;
  };
  const name = typeof attrs.name === 'string' ? attrs.name : undefined;
  const label = name ?? `${labelPrefix ?? ''}Road@(${path[0]},${path[1]})`;
  return {
    label,
    path,
    heights: perPoint(attrs.heights),
    widths: perPoint(attrs.widths),
    banks: perPoint(attrs.banks),
    isBridge:
      attrs['bridge-url'] !== undefined || boolAttr(attrs.bridge, false),
    flatten: boolAttr(attrs.flatten, true),
    maxGrade: Math.max(0, num(attrs['flatten-max-grade'], DEFAULT_MAX_GRADE)),
  };
}

function collectRoads(root: ParsedElement): RoadSpec[] {
  const out: RoadSpec[] = [];
  walk(root, (el) => {
    const tag = el.tagName?.toLowerCase();
    if (tag === 'road') {
      const road = roadFromElement(el);
      if (road) out.push(road);
    } else if (tag === 'roadnetwork') {
      let def;
      try {
        def = parseRoadNetworkElement(el);
      } catch {
        return; // parse errors are reported by checkRoadNetworks
      }
      const expanded = expandRoadNetworkToRoads(def);
      for (let i = 0; i < expanded.length; i++) {
        const road = roadFromElement(expanded[i]!, '[network] ');
        if (!road) continue;
        const seg = def.segments[i];
        if (seg) road.label = `[network] ${seg.a}→${seg.b}`;
        out.push(road);
      }
    }
  });
  return out;
}

/** Signed distance past the waterline (negative = on the wet surface). */
function waterDistance(w: WaterSpec, x: number, z: number): number {
  const d =
    w.kind === 'lake'
      ? Math.hypot(x - w.x, z - w.z)
      : distanceToPath(w.path, x, z);
  return d - w.shoreHalf;
}

interface WaterRun {
  body: WaterSpec;
  startArc: number;
  endArc: number;
}

/** Walk the polyline at ~SAMPLE_STEP_M (nodes included); visit(x, z, arc). */
function samplePath(
  road: RoadSpec,
  visit: (x: number, z: number, arc: number) => void
): void {
  let arc = 0;
  for (let i = 0; i + 3 < road.path.length; i += 2) {
    const ax = road.path[i]!;
    const az = road.path[i + 1]!;
    const bx = road.path[i + 2]!;
    const bz = road.path[i + 3]!;
    const len = Math.hypot(bx - ax, bz - az);
    const steps = Math.max(1, Math.ceil(len / SAMPLE_STEP_M));
    const step = len / steps;
    for (let s = 0; s < steps; s++) {
      const f = s / steps;
      visit(ax + (bx - ax) * f, az + (bz - az) * f, arc);
      arc += step;
    }
  }
  const last = road.path.length - 2;
  visit(road.path[last]!, road.path[last + 1]!, arc);
}

/** Maximal runs of the road inside each water surface (arc-length span). */
function waterRuns(road: RoadSpec, waters: WaterSpec[]): WaterRun[] {
  const runs: WaterRun[] = [];
  let current: WaterRun | null = null;
  samplePath(road, (x, z, sampleArc) => {
    let body: WaterSpec | null = null;
    for (const w of waters) {
      if (waterDistance(w, x, z) <= 0) {
        body = w;
        break;
      }
    }
    if (body && current && current.body === body) {
      current.endArc = sampleArc;
    } else {
      if (current) runs.push(current);
      current = body ? { body, startArc: sampleArc, endArc: sampleArc } : null;
    }
  });
  if (current) runs.push(current);
  return runs;
}

function checkWaterCrossings(
  road: RoadSpec,
  waters: WaterSpec[],
  issues: AnalyzeIssue[]
): void {
  if (waters.length === 0) return;
  let reported = 0;
  for (const run of waterRuns(road, waters)) {
    const length = run.endArc - run.startArc;
    if (length < MIN_WATER_RUN_M) continue;
    if (reported >= MAX_ISSUES_PER_ROAD) {
      issues.push({
        severity: 'info',
        code: 'road',
        message: `[Road] "${road.label}" has more water crossings (not shown)`,
      });
      return;
    }
    reported++;
    issues.push({
      severity: 'warn',
      code: 'road',
      message: `[Road] "${road.label}" runs ${length.toFixed(1)}m inside ${run.body.label} water surface (arc ${run.startArc.toFixed(1)}–${run.endArc.toFixed(1)}m) without a bridge profile`,
      detail: [
        road.flatten
          ? 'flatten is on: the road carve will re-fill the basin ("leaking lake")'
          : 'flatten is off: the painted ribbon still crosses the water',
        'cross with <Segment profile="bridge" bridge-url="…"> or move the path',
      ],
    });
  }
}

function checkBridgeOverNothing(
  road: RoadSpec,
  waters: WaterSpec[],
  issues: AnalyzeIssue[]
): void {
  if (waters.length === 0) {
    issues.push({
      severity: 'info',
      code: 'road',
      message: `[Road] bridge "${road.label}" crosses no water body (no <Lake>/<River> in the world) — stale bridge-url?`,
    });
    return;
  }
  let best = Infinity;
  samplePath(road, (x, z) => {
    for (const w of waters) {
      best = Math.min(best, waterDistance(w, x, z) - BRIDGE_LAND_REACH_M);
    }
  });
  if (!Number.isFinite(best) || best <= 0) return;
  issues.push({
    severity: 'info',
    code: 'road',
    message: `[Road] bridge "${road.label}" crosses no water body (nearest waterline ≥ ${best.toFixed(1)}m past the bank reach) — stale bridge-url?`,
  });
}

function checkListCounts(road: RoadSpec, issues: AnalyzeIssue[]): void {
  const pointCount = Math.floor(road.path.length / 2);
  const lists = [
    ['heights', road.heights],
    ['widths', road.widths],
    ['banks', road.banks],
  ] as const;
  for (const [name, list] of lists) {
    if (!list || list.length === pointCount) continue;
    issues.push({
      severity: 'error',
      code: 'road',
      message: `[Road] "${road.label}" ${name}= has ${list.length} values but path has ${pointCount} points (runtime parse throws)`,
    });
  }
}

function checkGrades(road: RoadSpec, issues: AnalyzeIssue[]): void {
  const heights = road.heights;
  if (!heights || road.maxGrade === 0) return;
  const pointCount = Math.floor(road.path.length / 2);
  if (heights.length !== pointCount) return; // reported by checkListCounts

  let runStart = -1;
  let runEnd = -1;
  let worstIdx = -1;
  let worstGrade = 0;
  let worstDy = 0;
  let worstDist = 0;
  const flush = () => {
    if (runStart < 0) return;
    const sign = worstDy >= 0 ? '+' : '';
    issues.push({
      severity: 'warn',
      code: 'road',
      message: `[Road] "${road.label}" grade ${(worstGrade * 100).toFixed(0)}% exceeds flatten-max-grade ${(road.maxGrade * 100).toFixed(0)}% (points ${runStart}–${runEnd}; worst ${worstIdx}→${worstIdx + 1}: Δh ${sign}${worstDy.toFixed(2)}m over ${worstDist.toFixed(1)}m)`,
    });
    runStart = -1;
    worstGrade = 0;
  };
  for (let i = 0; i + 1 < pointCount; i++) {
    const dx = road.path[i * 2 + 2]! - road.path[i * 2]!;
    const dz = road.path[i * 2 + 3]! - road.path[i * 2 + 1]!;
    const dist = Math.hypot(dx, dz);
    if (dist < DEGENERATE_M) continue;
    const dy = heights[i + 1]! - heights[i]!;
    const grade = Math.abs(dy) / dist;
    if (grade > road.maxGrade + 1e-6) {
      if (runStart < 0) runStart = i;
      runEnd = i + 1;
      if (grade > worstGrade) {
        worstGrade = grade;
        worstIdx = i;
        worstDy = dy;
        worstDist = dist;
      }
    } else {
      flush();
    }
  }
  flush();
}

function checkShape(road: RoadSpec, issues: AnalyzeIssue[]): void {
  const pointCount = Math.floor(road.path.length / 2);
  let reported = 0;
  for (let i = 1; i + 1 < pointCount && reported < MAX_ISSUES_PER_ROAD; i++) {
    const x0 = road.path[i * 2 - 2]!;
    const z0 = road.path[i * 2 - 1]!;
    const x1 = road.path[i * 2]!;
    const z1 = road.path[i * 2 + 1]!;
    const x2 = road.path[i * 2 + 2]!;
    const z2 = road.path[i * 2 + 3]!;
    const d0 = Math.hypot(x1 - x0, z1 - z0);
    const d1 = Math.hypot(x2 - x1, z2 - z1);
    if (d0 < DEGENERATE_M || d1 < DEGENERATE_M) {
      issues.push({
        severity: 'warn',
        code: 'road',
        message: `[Road] "${road.label}" near-zero-length segment at point ${i} (${Math.min(d0, d1).toFixed(3)}m) — breaks resampling`,
      });
      reported++;
      continue;
    }
    const dot = (x1 - x0) * (x2 - x1) + (z1 - z0) * (z2 - z1);
    const cross = (x1 - x0) * (z2 - z1) - (z1 - z0) * (x2 - x1);
    const turnDeg = (Math.atan2(Math.abs(cross), dot) * 180) / Math.PI;
    if (turnDeg > 180 - HAIRPIN_DEG) {
      issues.push({
        severity: 'warn',
        code: 'road',
        message: `[Road] "${road.label}" hairpin at point ${i} (reverses ${turnDeg.toFixed(0)}°)`,
      });
      reported++;
    }
  }
}

/**
 * Geometry-level road checks over the expanded tree (see module doc).
 * Complements `checkRoadNetworks` (graph-level checks).
 */
export function checkRoadGeometry(root: ParsedElement): AnalyzeIssue[] {
  const waters = collectWaterBodies(root);
  const roads = collectRoads(root);
  const issues: AnalyzeIssue[] = [];
  for (const road of roads) {
    checkListCounts(road, issues);
    checkGrades(road, issues);
    checkShape(road, issues);
    if (road.isBridge) checkBridgeOverNothing(road, waters, issues);
    else checkWaterCrossings(road, waters, issues);
  }
  return issues;
}
