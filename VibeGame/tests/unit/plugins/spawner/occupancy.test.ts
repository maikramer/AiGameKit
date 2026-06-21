import { describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import {
  isSpawnAreaFree,
  registerSpawnFootprint,
  clearSpawnOccupancy,
} from '../../../../src/plugins/spawner/occupancy';

describe('spawn occupancy registry', () => {
  it('rejects overlapping discs and accepts disjoint ones', () => {
    const state = new State();
    registerSpawnFootprint(state, 10, 10, 3);

    // Footprints keep a 0.6 SPAWN_CLEARANCE gap, so the no-overlap threshold is
    // 3 + 2 + 0.6 = 5.6, not just the bare radii sum (5).
    // overlapping: distance 4 < 5.6
    expect(isSpawnAreaFree(state, 14, 10, 2)).toBe(false);
    // touching the bare radii (distance 5) still falls inside the clearance
    expect(isSpawnAreaFree(state, 15, 10, 2)).toBe(false);
    // clear of the buffer: distance 6 > 5.6
    expect(isSpawnAreaFree(state, 16, 10, 2)).toBe(true);
    expect(isSpawnAreaFree(state, 20, 20, 2)).toBe(true);
  });

  it('treats an empty registry as free and ignores non-positive radii', () => {
    const state = new State();
    expect(isSpawnAreaFree(state, 0, 0, 5)).toBe(true);
    registerSpawnFootprint(state, 0, 0, 0);
    expect(isSpawnAreaFree(state, 0, 0, 5)).toBe(true);
  });

  it('clearSpawnOccupancy frees everything', () => {
    const state = new State();
    registerSpawnFootprint(state, 0, 0, 10);
    expect(isSpawnAreaFree(state, 1, 1, 1)).toBe(false);
    clearSpawnOccupancy(state);
    expect(isSpawnAreaFree(state, 1, 1, 1)).toBe(true);
  });
});
