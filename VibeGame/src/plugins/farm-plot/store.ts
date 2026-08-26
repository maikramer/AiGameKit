import type { State } from '../../core';
import { FarmGrid } from './components';
import type { CellRef, GridSpec } from './grid';
import { cellIndex } from './grid';

/**
 * Per-tile state for one {@link FarmGrid}, kept in side arrays keyed by
 * (State, entity) — the pattern `terrain/utils.ts` uses for terrain fields.
 *
 * Tiles are data, not entities: a 64×48 field is 3072 tiles ≈ 28 KB here,
 * versus 3072 entity rows marched by every transform pass and query.
 */
export interface FarmGridData {
  /** True once the setup system interned crops and allocated the arrays. */
  ready: boolean;
  cols: number;
  rows: number;
  /** Tile lifecycle: see FarmTileStates. */
  state: Uint8Array;
  /** Index into cropIds (-1 = none). */
  cropId: Int16Array;
  stage: Uint8Array;
  growthDays: Uint16Array;
  dryDays: Uint16Array;
  wateredToday: Uint8Array;
  regrowCount: Uint8Array;
  /** Render flush list: tile indices whose visual changed. */
  dirty: Uint8Array;
  dirtyList: number[];
  /** Crop ids interned at setup, alphabetical — the save format's alphabet. */
  cropIds: string[];
  /** Render bookkeeping: instance slot in the crop pool, -1 = none. */
  cropSlot: Int32Array;
  /** Which pool `cropSlot` was allocated in — releases must find the pool the
   *  slot was taken from, which is not the tile's current (crop, stage). */
  slotCrop: Int16Array;
  slotStage: Uint8Array;
}

const grids = new WeakMap<State, Map<number, FarmGridData>>();

function gridsFor(state: State): Map<number, FarmGridData> {
  let map = grids.get(state);
  if (!map) {
    map = new Map();
    grids.set(state, map);
  }
  return map;
}

/**
 * Allocate (or reuse) the side arrays for a grid. Reallocates when the
 * dimensions changed — deserialize loads saves that may predate a resize.
 */
export function ensureFarmGridData(
  state: State,
  eid: number,
  cols: number,
  rows: number
): FarmGridData {
  const map = gridsFor(state);
  const existing = map.get(eid);
  if (existing && existing.state.length === cols * rows) return existing;
  const tiles = cols * rows;
  const data: FarmGridData = {
    ready: false,
    cols,
    rows,
    state: new Uint8Array(tiles),
    cropId: new Int16Array(tiles).fill(-1),
    stage: new Uint8Array(tiles),
    growthDays: new Uint16Array(tiles),
    dryDays: new Uint16Array(tiles),
    wateredToday: new Uint8Array(tiles),
    regrowCount: new Uint8Array(tiles),
    dirty: new Uint8Array(tiles),
    dirtyList: [],
    cropIds: existing?.cropIds ?? [],
    cropSlot: new Int32Array(tiles).fill(-1),
    slotCrop: new Int16Array(tiles).fill(-1),
    slotStage: new Uint8Array(tiles),
  };
  map.set(eid, data);
  return data;
}

/** Drop a grid's side data (entity destroyed / world reloaded). */
export function releaseFarmGridData(state: State, eid: number): void {
  grids.get(state)?.delete(eid);
}

/** Side data for a grid, or null when the entity is not a farm grid. */
export function getFarmGridData(
  state: State,
  eid: number
): FarmGridData | null {
  const data = grids.get(state)?.get(eid);
  if (data) return data;
  return state.hasComponent(eid, FarmGrid)
    ? ensureFarmGridData(state, eid, 0, 0)
    : null;
}

/** All farm-grid eids that have side data (used by render cleanup sweeps). */
export function farmGridEntities(state: State): number[] {
  return Array.from(grids.get(state)?.keys() ?? []);
}

/** Mark one tile for the next render flush. */
export function markTileDirty(data: FarmGridData, index: number): void {
  if (data.dirty[index] === 0) {
    data.dirty[index] = 1;
    data.dirtyList.push(index);
  }
}

export function markAllTilesDirty(data: FarmGridData): void {
  for (let i = 0; i < data.state.length; i++) markTileDirty(data, i);
}

/** Snapshot of one tile for public reads. */
export interface FarmTile {
  col: number;
  row: number;
  state: number;
  cropId: string | null;
  stage: number;
  growthDays: number;
  dryDays: number;
  wateredToday: boolean;
  regrowCount: number;
}

export function readTile(
  spec: GridSpec,
  data: FarmGridData,
  col: number,
  row: number
): FarmTile | null {
  const index = cellIndex(spec, col, row);
  if (index < 0) return null;
  const cropIdx = data.cropId[index];
  return {
    col,
    row,
    state: data.state[index],
    cropId: cropIdx >= 0 ? (data.cropIds[cropIdx] ?? null) : null,
    stage: data.stage[index],
    growthDays: data.growthDays[index],
    dryDays: data.dryDays[index],
    wateredToday: data.wateredToday[index] === 1,
    regrowCount: data.regrowCount[index],
  };
}

/** GridSpec view over a FarmGrid entity's component values. */
export function specOf(_state: State, eid: number): GridSpec {
  return {
    originX: FarmGrid.originX[eid],
    originZ: FarmGrid.originZ[eid],
    cellSize: FarmGrid.cellSize[eid],
    cols: FarmGrid.cols[eid],
    rows: FarmGrid.rows[eid],
  };
}

export type { CellRef };
