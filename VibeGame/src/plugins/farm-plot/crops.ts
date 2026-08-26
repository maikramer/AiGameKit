import type { State } from '../../core';
import { FarmGrid } from './components';
import { cellIndex, type GridSpec } from './grid';
import {
  getFarmGridData,
  markTileDirty,
  specOf,
  type FarmGridData,
} from './store';

/**
 * Tile lifecycle:
 *
 *   Empty ──till──▶ Tilled ──plant──▶ Growing ──(totalDays watered)──▶ Ready
 *                      ▲                 │                       │
 *                      └── harvest (no regrow)                    │
 *                                         │ dryDays ≥ witherAfter  │ harvest (regrow)
 *                                         ▼                        │
 *                                      Withered ──clear── Empty    + Growing*
 *
 * Crops advance one growth day per slept day **only when watered that day**
 * (Harvest Moon SNES rule); unwatered days accumulate as dry days and can
 * wither the crop. Everything downstream (soil colour, crop meshes) derives
 * from these arrays; the engine layer never touches inventory or stamina.
 */

export const FarmTileStates = {
  Empty: 0,
  Tilled: 1,
  Growing: 2,
  Ready: 3,
  Withered: 4,
} as const;

export type FarmTileState =
  (typeof FarmTileStates)[keyof typeof FarmTileStates];

/**
 * Crop definition, `kind: crop` in the DataRegistry. Seasons are opaque
 * numbers so this plugin stays independent of any calendar plugin (the
 * daycycle plugin defines the canonical Season enum).
 */
export interface CropDef {
  id: string;
  /** Numeric seasons this crop may be planted in (spring=0 … winter=3). */
  seasons: number[];
  seedItemId: string;
  yieldItemId: string;
  yieldMin: number;
  yieldMax: number;
  /** In-season days per visual stage; total = growth requirement. */
  daysPerStage: number[];
  /** Days to regrow after a harvest; 0 = dies at harvest. */
  regrowDays: number;
  /** Consecutive dry days before withering; 0 = never withers. */
  witherAfterDays: number;
  /** Visual height (m) per stage — last entry is the mature look. */
  stageHeights: number[];
  /** Fallback tint for the procedural crop geometry. */
  color: number;
  /** Optional per-stage GLB URLs (empty = procedural geometry). */
  stageMeshes: string[];
}

const DEFAULT_COLOR = 0x3f8f3f;

/** Coerce a raw registry def into a CropDef with sane defaults. */
export function normalizeCropDef(raw: Record<string, unknown>): CropDef {
  const num = (v: unknown, d: number) =>
    typeof v === 'number' && Number.isFinite(v) ? v : d;
  const days = Array.isArray(raw.daysPerStage)
    ? (raw.daysPerStage as unknown[]).map((d) => num(d, 1))
    : [1];
  const stages = Math.max(1, days.length);
  const heights = Array.isArray(raw.stageHeights)
    ? (raw.stageHeights as unknown[]).map((h) => num(h, 0.2))
    : [];
  while (heights.length < stages) heights.push(0.15 + 0.15 * heights.length);
  return {
    id: String(raw.id ?? raw.seedItemId ?? 'crop'),
    seasons: Array.isArray(raw.seasons)
      ? (raw.seasons as unknown[]).map((s) => num(s, 0))
      : [0, 1, 2, 3],
    seedItemId: String(raw.seedItemId ?? `${raw.id}_seed`),
    yieldItemId: String(raw.yieldItemId ?? `${raw.id}_produce`),
    yieldMin: Math.max(1, num(raw.yieldMin, 1)),
    yieldMax: Math.max(1, num(raw.yieldMax, 2)),
    daysPerStage: days,
    regrowDays: Math.max(0, num(raw.regrowDays, 0)),
    witherAfterDays: Math.max(0, num(raw.witherAfterDays, 0)),
    stageHeights: heights,
    color: num(raw.color, DEFAULT_COLOR) | 0,
    stageMeshes: Array.isArray(raw.stageMeshes)
      ? (raw.stageMeshes as unknown[]).map(String)
      : [],
  };
}

export function totalGrowthDays(def: CropDef): number {
  return def.daysPerStage.reduce((a, b) => a + b, 0);
}

/** 0-based visual stage for a growth-day count; `daysPerStage.length` = mature. */
export function stageForGrowth(def: CropDef, growthDays: number): number {
  let acc = 0;
  for (let s = 0; s < def.daysPerStage.length; s++) {
    acc += def.daysPerStage[s];
    if (growthDays < acc) return s;
  }
  return def.daysPerStage.length;
}

export interface FarmDayReport {
  /** Growing tiles that earned a growth day (were watered). */
  grown: number;
  /** Growing tiles that ripened to Ready. */
  ripened: number;
  /** Tiles that withered from drought. */
  withered: number;
}

export interface HarvestYield {
  cropId: string;
  itemId: string;
  count: number;
  /** True when the crop regrows instead of dying at harvest. */
  regrown: boolean;
}

/** Mill the calendar forward one day over every tile of a grid. Pure w.r.t.
 *  ECS besides writes into the grid's own arrays — no inventory, no clock. */
export function advanceFarmDay(
  state: State,
  eid: number
): FarmDayReport | null {
  const data = getFarmGridData(state, eid);
  if (!data?.ready) return null;
  const report: FarmDayReport = { grown: 0, ripened: 0, withered: 0 };
  const defs = cropDefsFor(data);

  for (let i = 0; i < data.state.length; i++) {
    const tileState = data.state[i];
    if (tileState === FarmTileStates.Growing) {
      if (data.wateredToday[i] === 1) {
        data.growthDays[i]++;
        data.dryDays[i] = 0;
        report.grown++;
      } else {
        data.dryDays[i]++;
      }
      data.wateredToday[i] = 0;

      const def = defs[data.cropId[i]];
      if (def) {
        if (def.witherAfterDays > 0 && data.dryDays[i] >= def.witherAfterDays) {
          data.state[i] = FarmTileStates.Withered;
          data.stage[i] = 0;
          report.withered++;
        } else if (data.growthDays[i] >= totalGrowthDays(def)) {
          data.state[i] = FarmTileStates.Ready;
          data.stage[i] = def.daysPerStage.length;
          report.ripened++;
        } else {
          data.stage[i] = stageForGrowth(def, data.growthDays[i]);
        }
      }
      // Soil colour lost its "watered today" tint; stage may have moved.
      markTileDirty(data, i);
      notifyTileChanged(state, eid, i);
    } else if (
      tileState === FarmTileStates.Ready ||
      tileState === FarmTileStates.Tilled
    ) {
      // Ready fruit holds; tilled soil dries overnight.
      if (data.wateredToday[i] === 1) markTileDirty(data, i);
      data.wateredToday[i] = 0;
    }
  }
  FarmGrid.version[eid]++;
  return report;
}

// --- Mutators (all boolean; never touch inventory/stamina — that's the game's) ---

export function tillTile(
  state: State,
  eid: number,
  col: number,
  row: number
): boolean {
  const spec = specOf(state, eid);
  const data = getFarmGridData(state, eid);
  const i = data ? cellIndex(spec, col, row) : -1;
  if (!data?.ready || i < 0) return false;
  if (data.state[i] !== FarmTileStates.Empty) return false;
  data.state[i] = FarmTileStates.Tilled;
  data.wateredToday[i] = 0;
  FarmGrid.version[eid]++;
  markTileDirty(data, i);
  notifyTileChanged(state, eid, i);
  return true;
}

export function waterTile(
  state: State,
  eid: number,
  col: number,
  row: number
): boolean {
  const spec = specOf(state, eid);
  const data = getFarmGridData(state, eid);
  const i = data ? cellIndex(spec, col, row) : -1;
  if (!data?.ready || i < 0) return false;
  if (
    data.state[i] !== FarmTileStates.Tilled &&
    data.state[i] !== FarmTileStates.Growing
  ) {
    return false;
  }
  data.wateredToday[i] = 1;
  FarmGrid.version[eid]++;
  markTileDirty(data, i);
  notifyTileChanged(state, eid, i);
  return true;
}

export function plantSeed(
  state: State,
  eid: number,
  col: number,
  row: number,
  cropId: string
): boolean {
  const spec = specOf(state, eid);
  const data = getFarmGridData(state, eid);
  const i = data ? cellIndex(spec, col, row) : -1;
  if (!data?.ready || i < 0) return false;
  if (data.state[i] !== FarmTileStates.Tilled) return false;
  const idx = data.cropIds.indexOf(cropId);
  if (idx < 0) return false;
  data.state[i] = FarmTileStates.Growing;
  data.cropId[i] = idx;
  data.growthDays[i] = 0;
  data.dryDays[i] = 0;
  data.regrowCount[i] = 0;
  data.stage[i] = 0;
  FarmGrid.version[eid]++;
  markTileDirty(data, i);
  notifyTileChanged(state, eid, i);
  return true;
}

export function harvestTile(
  state: State,
  eid: number,
  col: number,
  row: number,
  rng: () => number = Math.random
): HarvestYield | null {
  const spec = specOf(state, eid);
  const data = getFarmGridData(state, eid);
  const i = data ? cellIndex(spec, col, row) : -1;
  if (!data?.ready || i < 0) return null;
  if (data.state[i] !== FarmTileStates.Ready) return null;
  const defs = cropDefsFor(data);
  const def = defs[data.cropId[i]];
  if (!def) return null;
  const span = Math.max(0, def.yieldMax - def.yieldMin);
  const count = def.yieldMin + Math.floor(rng() * (span + 1));
  const regrown = def.regrowDays > 0;
  if (regrown) {
    data.state[i] = FarmTileStates.Growing;
    data.growthDays[i] = Math.max(0, totalGrowthDays(def) - def.regrowDays);
    data.dryDays[i] = 0;
    data.regrowCount[i]++;
    data.stage[i] = stageForGrowth(def, data.growthDays[i]);
  } else {
    data.state[i] = FarmTileStates.Tilled;
    data.cropId[i] = -1;
    data.stage[i] = 0;
    data.regrowCount[i] = 0;
  }
  FarmGrid.version[eid]++;
  markTileDirty(data, i);
  notifyTileChanged(state, eid, i);
  return {
    cropId: def.id,
    itemId: def.yieldItemId,
    count,
    regrown,
  };
}

/** Return a tile to bare earth (clears withered crops, spent soil, anything). */
export function clearTile(
  state: State,
  eid: number,
  col: number,
  row: number
): boolean {
  const spec = specOf(state, eid);
  const data = getFarmGridData(state, eid);
  const i = data ? cellIndex(spec, col, row) : -1;
  if (!data?.ready || i < 0) return false;
  if (data.state[i] === FarmTileStates.Empty) return false;
  data.state[i] = FarmTileStates.Empty;
  data.cropId[i] = -1;
  data.stage[i] = 0;
  data.growthDays[i] = 0;
  data.dryDays[i] = 0;
  data.wateredToday[i] = 0;
  data.regrowCount[i] = 0;
  FarmGrid.version[eid]++;
  markTileDirty(data, i);
  notifyTileChanged(state, eid, i);
  return true;
}

// --- Crop defs available to a grid ---

/**
 * Interned defs parallel to `data.cropIds`. Stored on the data object as a
 * non-enumerable-ish plain field to keep the payload serializable by hand
 * (the serializer walks explicit arrays only, so a def list here is safe).
 */
export function attachCropDefs(
  data: FarmGridData,
  defs: (CropDef | undefined)[]
): void {
  (data as FarmGridData & { defs: (CropDef | undefined)[] }).defs = defs;
}

/** Defs parallel to `cropIds` (undefined entries = unknown id in a save). */
export function cropDefsFor(data: FarmGridData): (CropDef | undefined)[] {
  return (data as FarmGridData & { defs?: (CropDef | undefined)[] }).defs ?? [];
}

// --- Tile-changed listeners (local registry; must work without any event bus) ---

export type FarmTileListener = (eid: number, tileIndex: number) => void;

const listeners = new WeakMap<State, Set<FarmTileListener>>();

export function onFarmTileChanged(
  state: State,
  listener: FarmTileListener
): () => void {
  let set = listeners.get(state);
  if (!set) {
    set = new Set();
    listeners.set(state, set);
  }
  set.add(listener);
  return () => set!.delete(listener);
}

function notifyTileChanged(state: State, eid: number, tileIndex: number): void {
  const set = listeners.get(state);
  if (!set) return;
  for (const listener of set) listener(eid, tileIndex);
}

export type { GridSpec };
