import { describe, expect, it } from 'bun:test';
import { cellIndex, facingCellFrom, worldToCell } from 'vibegame';
// `cellToWorld` is not on the main barrel — the road plugin owns that name.
import {
  cellToWorld,
  quantizeForward,
  type GridSpec,
} from '../../../src/plugins/farm-plot/grid';

const SPEC: GridSpec = {
  originX: 0,
  originZ: 0,
  cellSize: 1,
  cols: 3,
  rows: 3,
};

describe('farm grid math', () => {
  describe('cellIndex', () => {
    it('indexes row-major inside the grid', () => {
      expect(cellIndex(SPEC, 0, 0)).toBe(0);
      expect(cellIndex(SPEC, 2, 0)).toBe(2);
      expect(cellIndex(SPEC, 0, 1)).toBe(3);
      expect(cellIndex(SPEC, 2, 2)).toBe(8);
    });
    it('rejects out-of-bounds cells', () => {
      expect(cellIndex(SPEC, -1, 0)).toBe(-1);
      expect(cellIndex(SPEC, 0, -1)).toBe(-1);
      expect(cellIndex(SPEC, 3, 0)).toBe(-1);
      expect(cellIndex(SPEC, 0, 3)).toBe(-1);
    });
  });

  describe('worldToCell / cellToWorld round-trip', () => {
    it('round-trips every cell of the grid', () => {
      for (let row = 0; row < SPEC.rows; row++) {
        for (let col = 0; col < SPEC.cols; col++) {
          const world = cellToWorld(SPEC, col, row)!;
          const back = worldToCell(SPEC, world.x, world.z)!;
          expect(back).toEqual({ col, row });
          // Points inside the cell (not just its centre) map back too.
          const inner = worldToCell(SPEC, world.x + 0.49, world.z - 0.49)!;
          expect(inner).toEqual({ col, row });
        }
      }
    });

    it('returns null outside the grid', () => {
      expect(worldToCell(SPEC, -0.51, 0)).toBeNull();
      expect(worldToCell(SPEC, 0, 2.51)).toBeNull();
    });

    it('honours origin and cell size', () => {
      const spec: GridSpec = {
        originX: 100,
        originZ: -50,
        cellSize: 2,
        cols: 4,
        rows: 2,
      };
      const world = cellToWorld(spec, 2, 1)!;
      expect(world).toEqual({ x: 104, z: -48 });
      expect(worldToCell(spec, 104.9, -47.5)).toEqual({ col: 2, row: 1 });
    });
  });

  describe('quantizeForward', () => {
    it('snaps to the dominant cardinal', () => {
      expect(quantizeForward(0.99, 0.01)).toEqual({ col: 1, row: 0 });
      expect(quantizeForward(-0.8, 0.3)).toEqual({ col: -1, row: 0 });
      expect(quantizeForward(0.2, 0.98)).toEqual({ col: 0, row: 1 });
      expect(quantizeForward(0.1, -0.9)).toEqual({ col: 0, row: -1 });
    });
    it('breaks ties towards the Z axis (engine facing convention)', () => {
      expect(quantizeForward(0.7, 0.7)).toEqual({ col: 0, row: 1 });
      expect(quantizeForward(-0.7, -0.7)).toEqual({ col: 0, row: -1 });
      expect(quantizeForward(0, 0)).toEqual({ col: 0, row: 1 });
    });
  });

  describe('facingCellFrom', () => {
    it('returns the cardinal neighbour of the actor cell (all 4 directions)', () => {
      const centre = { px: 1, pz: 1 };
      expect(facingCellFrom(centre.px, centre.pz, 0, 1, SPEC)).toEqual({
        col: 1,
        row: 2,
      });
      expect(facingCellFrom(centre.px, centre.pz, 0, -1, SPEC)).toEqual({
        col: 1,
        row: 0,
      });
      expect(facingCellFrom(centre.px, centre.pz, 1, 0, SPEC)).toEqual({
        col: 2,
        row: 1,
      });
      expect(facingCellFrom(centre.px, centre.pz, -1, 0, SPEC)).toEqual({
        col: 0,
        row: 1,
      });
    });

    it('quantizes diagonals to the dominant axis, ties to Z', () => {
      expect(facingCellFrom(1, 1, 0.9, 0.1, SPEC)).toEqual({ col: 2, row: 1 });
      expect(facingCellFrom(1, 1, 0.7, 0.7, SPEC)).toEqual({ col: 1, row: 2 });
      expect(facingCellFrom(1, 1, -0.6, -0.8, SPEC)).toEqual({
        col: 1,
        row: 0,
      });
    });

    it('falls back to the actor cell when facing off the field', () => {
      expect(facingCellFrom(1, 2, 0, 1, SPEC)).toEqual({ col: 1, row: 2 });
    });

    it('an actor beside the field reaches the border tile', () => {
      expect(facingCellFrom(1, -1.4, 0, 1, SPEC)).toEqual({ col: 1, row: 0 });
      expect(facingCellFrom(-1.4, 1, 1, 0, SPEC)).toEqual({ col: 0, row: 1 });
    });

    it('returns null when nothing is in reach', () => {
      expect(facingCellFrom(10, 10, 0, 1, SPEC)).toBeNull();
    });
  });
});
