import type { AnalyzeIssue, Footprint } from './types';

const OVERLAP_EPS_M2 = 0.05;
const Y_EPS = 0.01;

const SOLID_KINDS = new Set(['composition', 'gameobject', 'other']);
const GROUND_KINDS = new Set(['pad', 'road']);

function overlapArea(a: Footprint, b: Footprint): number {
  const ox = Math.min(a.maxX, b.maxX) - Math.max(a.minX, b.minX);
  const oz = Math.min(a.maxZ, b.maxZ) - Math.max(a.minZ, b.minZ);
  if (ox <= 0 || oz <= 0) return 0;
  return ox * oz;
}

function yIntervalsOverlap(a: Footprint, b: Footprint): boolean {
  return a.minY - Y_EPS <= b.maxY && b.minY - Y_EPS <= a.maxY;
}

function fmtSize(fp: Footprint): string {
  const w = fp.maxX - fp.minX;
  const d = fp.maxZ - fp.minZ;
  return `${w.toFixed(1)}×${d.toFixed(1)}`;
}

function fmtCenter(fp: Footprint): string {
  const x = (fp.minX + fp.maxX) / 2;
  const z = (fp.minZ + fp.maxZ) / 2;
  return `(${x.toFixed(1)}, ${z.toFixed(1)})`;
}

function isSolid(fp: Footprint): boolean {
  return SOLID_KINDS.has(fp.kind);
}

function isGround(fp: Footprint): boolean {
  return GROUND_KINDS.has(fp.kind);
}

type PairHit = {
  A: Footprint;
  B: Footprint;
  area: number;
  ox: number;
  oz: number;
};

function collectBestPairs(
  footprints: Footprint[],
  cellSize: number,
  accept: (a: Footprint, b: Footprint) => boolean
): Map<string, PairHit> {
  const buckets = new Map<string, number[]>();
  const key = (ix: number, iz: number) => `${ix},${iz}`;

  for (let i = 0; i < footprints.length; i++) {
    const fp = footprints[i]!;
    const ix0 = Math.floor(fp.minX / cellSize);
    const ix1 = Math.floor(fp.maxX / cellSize);
    const iz0 = Math.floor(fp.minZ / cellSize);
    const iz1 = Math.floor(fp.maxZ / cellSize);
    for (let ix = ix0; ix <= ix1; ix++) {
      for (let iz = iz0; iz <= iz1; iz++) {
        const k = key(ix, iz);
        let list = buckets.get(k);
        if (!list) {
          list = [];
          buckets.set(k, list);
        }
        list.push(i);
      }
    }
  }

  const seenPairs = new Set<string>();
  const bestByGroup = new Map<string, PairHit>();

  for (const list of buckets.values()) {
    for (let a = 0; a < list.length; a++) {
      for (let b = a + 1; b < list.length; b++) {
        const i = list[a]!;
        const j = list[b]!;
        if (i === j) continue;
        const lo = Math.min(i, j);
        const hi = Math.max(i, j);
        const pair = `${lo}:${hi}`;
        if (seenPairs.has(pair)) continue;
        seenPairs.add(pair);

        const A = footprints[lo]!;
        const B = footprints[hi]!;
        if (A.groupId && A.groupId === B.groupId) continue;
        if (!accept(A, B)) continue;
        const area = overlapArea(A, B);
        if (area < OVERLAP_EPS_M2) continue;

        const gA = A.groupId ?? A.id;
        const gB = B.groupId ?? B.id;
        const gKey = gA < gB ? `${gA}|${gB}` : `${gB}|${gA}`;
        const ox = Math.min(A.maxX, B.maxX) - Math.max(A.minX, B.minX);
        const oz = Math.min(A.maxZ, B.maxZ) - Math.max(A.minZ, B.minZ);
        const prev = bestByGroup.get(gKey);
        if (!prev || area > prev.area) {
          bestByGroup.set(gKey, { A, B, area, ox, oz });
        }
      }
    }
  }

  return bestByGroup;
}

/**
 * Solid↔solid overlaps when XZ and Y intervals intersect.
 */
export function findSolidOverlaps(
  footprints: Footprint[],
  cellSize = 8
): AnalyzeIssue[] {
  const solids = footprints.filter(isSolid);
  const issues: AnalyzeIssue[] = [];
  if (solids.length < 2) return issues;

  const best = collectBestPairs(solids, cellSize, (A, B) =>
    yIntervalsOverlap(A, B)
  );

  for (const { A, B, area, ox, oz } of best.values()) {
    const depth = Math.min(ox, oz);
    const allow = Math.max(A.overlapMax ?? 0, B.overlapMax ?? 0);
    if (allow > 0 && depth <= allow) continue;

    const labelA = A.label.replace(/#\d+$/, '');
    const labelB = B.label.replace(/#\d+$/, '');
    issues.push({
      severity: 'error',
      code: 'overlap',
      message: '[analyze] ERROR overlap solid',
      detail: [
        `  A: ${labelA} @ ${fmtCenter(A)} size≈${fmtSize(A)}`,
        `  B: ${labelB} @ ${fmtCenter(B)} size≈${fmtSize(B)}`,
        `  overlap≈${area.toFixed(1)} m²  (Δx=${ox.toFixed(1)} Δz=${oz.toFixed(1)} depth=${depth.toFixed(2)})`,
      ],
    });
  }

  return issues;
}

/**
 * Solid∩(pad|road) XZ overlaps — warn (ground vs solid).
 */
export function findGroundOverlaps(
  footprints: Footprint[],
  cellSize = 8
): AnalyzeIssue[] {
  const issues: AnalyzeIssue[] = [];
  const mixed = footprints.filter((f) => isSolid(f) || isGround(f));
  if (mixed.length < 2) return issues;

  const best = collectBestPairs(
    mixed,
    cellSize,
    (A, B) => (isSolid(A) && isGround(B)) || (isGround(A) && isSolid(B))
  );

  for (const { A, B, area, ox, oz } of best.values()) {
    const labelA = A.label.replace(/#\d+$/, '');
    const labelB = B.label.replace(/#\d+$/, '');
    issues.push({
      severity: 'warn',
      code: 'overlap',
      message: '[analyze] WARN overlap solid∩ground',
      detail: [
        `  A: ${labelA} (${A.kind}) @ ${fmtCenter(A)} size≈${fmtSize(A)}`,
        `  B: ${labelB} (${B.kind}) @ ${fmtCenter(B)} size≈${fmtSize(B)}`,
        `  overlap≈${area.toFixed(1)} m²  (Δx=${ox.toFixed(1)} Δz=${oz.toFixed(1)})`,
      ],
    });
  }

  return issues;
}
