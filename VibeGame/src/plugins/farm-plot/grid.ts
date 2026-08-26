/**
 * Pure grid math for farm plots — no ECS, no THREE, trivially testable.
 *
 * Cell (col, row) spans world x ∈ [originX + (col−½)·cs, originX + (col+½)·cs)
 * and likewise for z — i.e. `originX/originZ` is the CENTRE of cell (0,0),
 * matching how `<TerrainPad at>` names the centre of its footprint.
 */

export interface CellRef {
  col: number;
  row: number;
}

export interface GridSpec {
  originX: number;
  originZ: number;
  cellSize: number;
  cols: number;
  rows: number;
}

/** Flat tile index; -1 when (col,row) is outside the grid. */
export function cellIndex(
  spec: Pick<GridSpec, 'cols' | 'rows'>,
  col: number,
  row: number
): number {
  if (col < 0 || row < 0 || col >= spec.cols || row >= spec.rows) return -1;
  return row * spec.cols + col;
}

/** Cell containing world point (x,z); null outside the grid. */
export function worldToCell(
  spec: GridSpec,
  x: number,
  z: number
): CellRef | null {
  const cs = spec.cellSize || 1;
  const fx = (x - spec.originX) / cs + 0.5;
  const fz = (z - spec.originZ) / cs + 0.5;
  const col = Math.floor(fx);
  const row = Math.floor(fz);
  // A point exactly on the east/south border belongs to the next cell only
  // when that cell exists; the bound checks fold it back otherwise.
  if (col < 0 || row < 0 || col >= spec.cols || row >= spec.rows) return null;
  return { col, row };
}

/** World-space centre of a cell; null outside the grid. */
export function cellToWorld(
  spec: GridSpec,
  col: number,
  row: number
): { x: number; z: number } | null {
  if (cellIndex(spec, col, row) < 0) return null;
  const cs = spec.cellSize || 1;
  return { x: spec.originX + col * cs, z: spec.originZ + row * cs };
}

/**
 * Snap an arbitrary direction to the dominant cardinal axis.
 *
 * Ties (|fx| = |fz|, including the degenerate zero vector) resolve to the Z
 * axis: the engine's facing convention is `atan2(input.x, input.z)`, so the
 * neutral heading is +Z, not a diagonal.
 */
export function quantizeForward(fx: number, fz: number): CellRef {
  if (Math.abs(fx) > Math.abs(fz)) {
    return { col: fx > 0 ? 1 : -1, row: 0 };
  }
  return { col: 0, row: fz >= 0 ? 1 : -1 };
}

/**
 * The tile the actor at (px,pz) is working: the cardinal neighbour of the cell
 * they stand on (Harvest-Moon "tile à frente"). Standing off the field and
 * facing in still finds the boundary tile — probing one cell ahead of the
 * feet. Returns null when no in-grid tile is in reach.
 */
export function facingCellFrom(
  px: number,
  pz: number,
  fx: number,
  fz: number,
  spec: GridSpec
): CellRef | null {
  const step = quantizeForward(fx, fz);
  const own = worldToCell(spec, px, pz);
  if (own) {
    const ahead: CellRef = { col: own.col + step.col, row: own.row + step.row };
    if (cellIndex(spec, ahead.col, ahead.row) >= 0) return ahead;
    // Facing off the field from the border: work the tile under the feet.
    return own;
  }
  const cs = spec.cellSize || 1;
  return worldToCell(spec, px + step.col * cs, pz + step.row * cs);
}
