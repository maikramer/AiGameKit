import type { State } from '../../core';
import { defineQuery } from '../../core';
import { FarmGrid } from './components';
import {
  getFarmGridData,
  readTile,
  specOf,
  type FarmGridData,
  type FarmTile,
} from './store';

const farmGridQuery = defineQuery([FarmGrid]);

/**
 * Convenience surface for game code. The systems do the heavy lifting; these
 * wrappers keep call sites terse (`tillTile(state, grid, col, row)` needs an
 * eid — `getFarmGrid(state)` finds the single-grid case).
 */

export interface CreateFarmGridOptions {
  /** Centre of cell (0,0) — the grid's north-west corner. */
  atX: number;
  atZ: number;
  cols: number;
  rows: number;
  cellSize?: number;
  /** Omit/0 to sample the ground at `at` during setup. */
  baseY?: number;
}

/**
 * Create a farm grid entity. The setup system (next `setup` pass, or call
 * `FarmGridSetupSystem.update!(state)` directly in tests) interns crops and
 * allocates the tile arrays — mutators return false until then.
 */
export function createFarmGrid(
  state: State,
  opts: CreateFarmGridOptions
): number {
  const eid = state.createEntity();
  state.addComponent(eid, FarmGrid, {
    originX: opts.atX,
    originZ: opts.atZ,
    cellSize: opts.cellSize ?? 1,
    baseY: opts.baseY ?? 0,
    cols: opts.cols,
    rows: opts.rows,
  });
  return eid;
}

/** The first initialized farm grid, or null. */
export function getFarmGrid(
  state: State
): { eid: number; data: FarmGridData } | null {
  for (const eid of farmGridQuery(state.world)) {
    const data = getFarmGridData(state, eid);
    if (data?.ready) return { eid, data };
  }
  return null;
}

/** Snapshot of one tile; null outside the grid. */
export function getTileState(
  state: State,
  eid: number,
  col: number,
  row: number
): FarmTile | null {
  return readTile(specOf(state, eid), getFarmGridData(state, eid)!, col, row);
}
