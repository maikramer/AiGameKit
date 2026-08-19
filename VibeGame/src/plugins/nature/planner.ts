import { logger } from '../../core/utils/logger';
import type { State } from '../../core';
import { isSpawnAreaFree } from '../spawner/occupancy';
import { sampleSiteFeatures } from './features';
import {
  hasNearCondition,
  matchesWhere,
  type NatureRulesPlan,
  type SiteFeatures,
  type SpeciesRule,
  type WhereCondition,
} from './rules';

/**
 * Deterministic rule planner: dart-throw spaced candidates across the region,
 * weighted species pick per site, composite groves, then a dedicated pass for
 * adjacency species (mushrooms under oaks) with a budget proportional to
 * their weight share. Emits one point bucket per species id.
 */

/** Hard ceiling so a runaway density never freezes boot. */
const MAX_SCATTER_CANDIDATES = 30_000;

function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a += 0x6d2b79f5;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Square grid hash for min-spacing dart throwing (radius ≤ cell size). */
class SpacingGrid {
  private readonly cells = new Map<string, Array<[number, number]>>();
  constructor(private readonly cell: number) {}

  private cellKey(x: number, z: number): string {
    return `${Math.floor(x / this.cell)},${Math.floor(z / this.cell)}`;
  }

  hasWithin(x: number, z: number, radius: number): boolean {
    const cx = Math.floor(x / this.cell);
    const cz = Math.floor(z / this.cell);
    const r2 = radius * radius;
    for (let dz = -1; dz <= 1; dz++) {
      for (let dx = -1; dx <= 1; dx++) {
        const list = this.cells.get(`${cx + dx},${cz + dz}`);
        if (!list) continue;
        for (const [px, pz] of list) {
          const ddx = px - x;
          const ddz = pz - z;
          if (ddx * ddx + ddz * ddz <= r2) return true;
        }
      }
    }
    return false;
  }

  add(x: number, z: number): void {
    const key = this.cellKey(x, z);
    let list = this.cells.get(key);
    if (!list) {
      list = [];
      this.cells.set(key, list);
    }
    list.push([x, z]);
  }
}

/** Per-species planned-point index backing the `near` adjacency queries. */
class SpeciesPointIndex {
  private readonly cells = new Map<
    string,
    Array<{ species: string; x: number; z: number }>
  >();
  constructor(private readonly cell: number) {}

  add(species: string, x: number, z: number): void {
    const key = `${Math.floor(x / this.cell)},${Math.floor(z / this.cell)}`;
    let list = this.cells.get(key);
    if (!list) {
      list = [];
      this.cells.set(key, list);
    }
    list.push({ species, x, z });
  }

  /** Nearest planned distance per requested species (Infinity when none). */
  nearestDistances(
    speciesIds: string[],
    x: number,
    z: number,
    maxRadius: number
  ): Map<string, number> {
    const out = new Map<string, number>();
    for (const sid of speciesIds) out.set(sid, Infinity);
    const cx = Math.floor(x / this.cell);
    const cz = Math.floor(z / this.cell);
    const span = Math.max(1, Math.ceil(maxRadius / this.cell));
    const r2 = maxRadius * maxRadius;
    for (let dz = -span; dz <= span; dz++) {
      for (let dx = -span; dx <= span; dx++) {
        const list = this.cells.get(`${cx + dx},${cz + dz}`);
        if (!list) continue;
        for (const p of list) {
          const best = out.get(p.species);
          if (best === undefined) continue;
          const ddx = p.x - x;
          const ddz = p.z - z;
          const d2 = ddx * ddx + ddz * ddz;
          if (d2 <= r2 && d2 < best * best) out.set(p.species, Math.sqrt(d2));
        }
      }
    }
    return out;
  }
}

/** Uniform-area point in the annulus [rMin, rMax] around a centre. */
function annulusPoint(
  cx: number,
  cz: number,
  rMin: number,
  rMax: number,
  rand: () => number
): [number, number] {
  const ang = rand() * Math.PI * 2;
  const rMinSq = rMin * rMin;
  const rMaxSq = rMax * rMax;
  const r = Math.sqrt(rMinSq + rand() * Math.max(0, rMaxSq - rMinSq));
  return [cx + Math.cos(ang) * r, cz + Math.sin(ang) * r];
}

function isCapped(species: SpeciesRule, count: number): boolean {
  return species.cap > 0 && count >= species.cap;
}

/**
 * Weighted pick among eligible species for one site. `nearDists` is only
 * passed in the near phase (adjacency species); without it any `near`
 * condition fails, which is what the scatter phase relies on.
 */
function pickSpecies(
  candidates: SpeciesRule[],
  bucketCounts: Map<string, number>,
  features: SiteFeatures,
  rand: () => number,
  nearDists?: ReadonlyMap<string, number>
): SpeciesRule | null {
  let total = 0;
  const eligible: SpeciesRule[] = [];
  const weights: number[] = [];
  for (const s of candidates) {
    if (isCapped(s, bucketCounts.get(s.id) ?? 0)) continue;
    if (!matchesWhere(s.where, features, nearDists)) continue;
    eligible.push(s);
    weights.push(s.weight);
    total += s.weight;
  }
  if (eligible.length === 0) return null;
  let r = rand() * total;
  for (let i = 0; i < eligible.length; i++) {
    r -= weights[i]!;
    if (r <= 0) return eligible[i]!;
  }
  return eligible[eligible.length - 1]!;
}

export interface NatureSpawnPlanResult {
  /** Species id → planned world XZ points (one entry per instance). */
  buckets: Map<string, Array<[number, number]>>;
}

function whereMatchesAt(
  state: State,
  where: WhereCondition,
  x: number,
  z: number,
  opts: { noiseScale: number; noiseSeed: number }
): boolean {
  const features = sampleSiteFeatures(state, x, z, opts);
  return features !== null && matchesWhere(where, features);
}

export function planNatureSpawns(
  state: State,
  plan: NatureRulesPlan
): NatureSpawnPlanResult {
  const rand = mulberry32(plan.seed >>> 0);
  const noiseOpts = { noiseScale: plan.noiseScale, noiseSeed: plan.seed };
  const minX = plan.regionMin[0];
  const maxX = plan.regionMax[0];
  const minZ = plan.regionMin[2];
  const maxZ = plan.regionMax[2];

  let total = Math.round(
    plan.spawnCountMode === 'density'
      ? (plan.densityPerKm2 * ((maxX - minX) * (maxZ - minZ))) / 1_000_000
      : plan.count
  );
  if (total > MAX_SCATTER_CANDIDATES) {
    logger.warn(
      `[nature] ${total} candidates capped to ${MAX_SCATTER_CANDIDATES} — lower density-per-km2 or shrink the region`
    );
    total = MAX_SCATTER_CANDIDATES;
  }

  const speciesById = new Map(plan.species.map((s) => [s.id, s]));
  const buckets = new Map<string, Array<[number, number]>>();
  for (const s of plan.species) buckets.set(s.id, []);
  const bucketCounts = new Map<string, number>();
  for (const s of plan.species) bucketCounts.set(s.id, 0);

  const scatterSpecies = plan.species.filter(
    (s) => s.weight > 0 && !hasNearCondition(s.where)
  );
  const nearSpecies = plan.species.filter(
    (s) => s.weight > 0 && hasNearCondition(s.where)
  );

  // Adjacency species run in their own pass AFTER their hosts exist, so they
  // get a candidate budget proportional to their weight share instead of
  // fighting for phase-1 leftovers (a dominant host would starve them).
  const scatterWeight = scatterSpecies.reduce((sum, s) => sum + s.weight, 0);
  const nearWeight = nearSpecies.reduce((sum, s) => sum + s.weight, 0);
  const scatterTotal =
    nearWeight > 0
      ? Math.round((total * scatterWeight) / (scatterWeight + nearWeight))
      : total;
  const nearTotal = total - scatterTotal;

  // Phase 1 — spaced candidates, one weighted species pick per site.
  const spacing = new SpacingGrid(Math.max(0.01, plan.minSpacing));
  let placed = 0;
  const maxAttempts = total * 12 + 256;
  for (
    let attempt = 0;
    attempt < maxAttempts && placed < scatterTotal;
    attempt++
  ) {
    const x = minX + rand() * (maxX - minX);
    const z = minZ + rand() * (maxZ - minZ);
    if (spacing.hasWithin(x, z, plan.minSpacing)) continue;
    // Honour explicit exclusions / earlier props (hand-placed village, pads).
    if (!isSpawnAreaFree(state, x, z, 0.5)) continue;
    const f = sampleSiteFeatures(state, x, z, noiseOpts);
    if (!f) continue;
    spacing.add(x, z);
    placed++;
    const picked = pickSpecies(scatterSpecies, bucketCounts, f, rand);
    if (picked) {
      buckets.get(picked.id)!.push([x, z]);
      bucketCounts.set(picked.id, bucketCounts.get(picked.id)! + 1);
    }
  }

  // Phase 2 — composite groves (mixed species around shared hubs).
  const planned = new SpeciesPointIndex(16);
  for (const s of plan.species) {
    for (const p of buckets.get(s.id)!) planned.add(s.id, p[0], p[1]);
  }
  for (const grove of plan.groves) {
    const hubSpacing = new SpacingGrid(Math.max(plan.minSpacing, grove.radius));
    let hubs = 0;
    const hubAttempts = grove.count * 16 + 64;
    for (let a = 0; a < hubAttempts && hubs < grove.count; a++) {
      const hx = minX + rand() * (maxX - minX);
      const hz = minZ + rand() * (maxZ - minZ);
      if (hubSpacing.hasWithin(hx, hz, grove.radius)) continue;
      if (!isSpawnAreaFree(state, hx, hz, 1)) continue;
      if (!whereMatchesAt(state, grove.where, hx, hz, noiseOpts)) continue;
      hubSpacing.add(hx, hz);
      hubs++;
      for (const member of grove.members) {
        const species = speciesById.get(member.species);
        if (!species || isCapped(species, bucketCounts.get(species.id) ?? 0)) {
          continue;
        }
        const n =
          member.countMin +
          Math.floor(rand() * (member.countMax - member.countMin + 1));
        for (let i = 0; i < n; i++) {
          const [px, pz] = annulusPoint(
            hx,
            hz,
            member.ringMin * grove.radius,
            member.ringMax * grove.radius,
            rand
          );
          // Member points still honour their own species rules (a rock that
          // needs a steep bank is dropped on flat grove ground).
          if (!whereMatchesAt(state, species.where, px, pz, noiseOpts)) {
            continue;
          }
          buckets.get(member.species)!.push([px, pz]);
          bucketCounts.set(
            member.species,
            bucketCounts.get(member.species)! + 1
          );
          planned.add(member.species, px, pz);
          if (isCapped(species, bucketCounts.get(species.id)!)) break;
        }
      }
    }
    if (hubs < grove.count) {
      logger.warn(
        `[nature] grove "${grove.id}" placed ${hubs}/${grove.count} hubs — loosen its <Where> or grow the region`
      );
    }
  }

  // Phase 3 — adjacency species get their own budget, anchored on their
  // hosts: each candidate is drawn in the annulus around a random host
  // instance instead of region-wide darts (their habitat — under canopies,
  // shore rings — is a tiny slice of the region and uniform sampling misses).
  if (nearSpecies.length > 0 && nearTotal > 0) {
    const nearIds = new Set(
      nearSpecies.flatMap((s) => s.where.nearSpecies ?? [])
    );
    const hostPool: Array<{ species: string; x: number; z: number }> = [];
    for (const s of plan.species) {
      if (!nearIds.has(s.id)) continue;
      for (const [px, pz] of buckets.get(s.id)!)
        hostPool.push({ species: s.id, x: px, z: pz });
    }
    if (hostPool.length > 0) {
      const maxNearRadius = Math.max(
        ...nearSpecies.map((s) =>
          Number.isFinite(s.where.nearDist?.max ?? Infinity)
            ? (s.where.nearDist!.max as number)
            : 50
        ),
        8
      );
      let nearPlaced = 0;
      for (
        let attempt = 0;
        attempt < maxAttempts && nearPlaced < nearTotal;
        attempt++
      ) {
        const host = hostPool[Math.floor(rand() * hostPool.length)]!;
        const band = annulusBandFor(nearSpecies, host.species);
        const [x, z] = annulusPoint(host.x, host.z, band.min, band.max, rand);
        if (x < minX || x > maxX || z < minZ || z > maxZ) continue;
        if (spacing.hasWithin(x, z, plan.minSpacing)) continue;
        if (!isSpawnAreaFree(state, x, z, 0.5)) continue;
        const f = sampleSiteFeatures(state, x, z, noiseOpts);
        if (!f) continue;
        spacing.add(x, z);
        nearPlaced++;
        const dists = planned.nearestDistances(
          [...nearIds],
          x,
          z,
          maxNearRadius + 1
        );
        const picked = pickSpecies(nearSpecies, bucketCounts, f, rand, dists);
        if (picked) {
          buckets.get(picked.id)!.push([x, z]);
          bucketCounts.set(picked.id, bucketCounts.get(picked.id)! + 1);
          planned.add(picked.id, x, z);
        }
      }
    }
  }

  return { buckets };
}

/** Effective near-band used to draw a candidate around a specific host species. */
function annulusBandFor(
  nearSpecies: SpeciesRule[],
  hostSpecies: string
): { min: number; max: number } {
  const bands = nearSpecies
    .filter((s) => s.where.nearSpecies?.includes(hostSpecies))
    .map((s) => s.where.nearDist!)
    .filter(Boolean);
  if (bands.length === 0) return { min: 0, max: 8 };
  const min = Math.min(...bands.map((b) => b.min));
  const max = Math.max(
    ...bands.map((b) => (Number.isFinite(b.max) ? b.max : 50))
  );
  return { min: Math.max(0, min), max: Math.max(min, max) };
}
