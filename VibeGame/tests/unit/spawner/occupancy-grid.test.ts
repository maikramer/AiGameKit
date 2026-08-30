import { describe, expect, it } from 'bun:test';
import { State } from 'aigamekit-vibegame';
import {
  clearSpawnOccupancy,
  isSpawnAreaFree,
  registerSpawnFootprint,
} from '../../../src/plugins/spawner/occupancy';

/**
 * The occupancy registry is written and read in the same pass — every instance
 * the spawner plants is tested against everything planted before it — so it is
 * bucketed by XZ cell. The grid must answer exactly what the old linear scan
 * answered, including for discs far larger than a cell (a `<SpawnExclusion>`
 * over a whole village).
 */
const CLEARANCE = 0.6;

function brute(
  discs: Array<[number, number, number]>,
  x: number,
  z: number,
  radius: number
): boolean {
  for (const [fx, fz, fr] of discs) {
    const minDist = fr + radius + CLEARANCE;
    if ((fx - x) ** 2 + (fz - z) ** 2 < minDist * minDist) return false;
  }
  return true;
}

describe('spawn occupancy grid', () => {
  it('matches a brute-force scan over a mix of disc sizes', () => {
    const state = new State();
    const discs: Array<[number, number, number]> = [];
    let seed = 12345;
    const rand = () => {
      seed = (seed * 1664525 + 1013904223) >>> 0;
      return seed / 0x100000000;
    };
    for (let i = 0; i < 200; i++) {
      const x = (rand() - 0.5) * 400;
      const z = (rand() - 0.5) * 400;
      // Deliberately spans sub-cell props and multi-cell exclusion zones.
      const r = rand() < 0.1 ? 5 + rand() * 30 : 0.3 + rand() * 2;
      discs.push([x, z, r]);
      registerSpawnFootprint(state, x, z, r);
    }

    let blocked = 0;
    for (let i = 0; i < 3000; i++) {
      const x = (rand() - 0.5) * 420;
      const z = (rand() - 0.5) * 420;
      const r = 0.2 + rand() * 3;
      const expected = brute(discs, x, z, r);
      expect(isSpawnAreaFree(state, x, z, r)).toBe(expected);
      if (!expected) blocked++;
    }
    expect(blocked).toBeGreaterThan(100);
  });

  it('sees a disc far bigger than one cell from well inside it', () => {
    // The first version of the grid reached out by the widest registered
    // radius, which was both slow and unnecessary; this is the case that
    // proves the cheaper reach is still correct.
    const state = new State();
    registerSpawnFootprint(state, 0, 0, 60);
    expect(isSpawnAreaFree(state, 0, 0, 0.5)).toBe(false);
    expect(isSpawnAreaFree(state, 40, 0, 0.5)).toBe(false);
    expect(isSpawnAreaFree(state, 59, 0, 0.5)).toBe(false);
    expect(isSpawnAreaFree(state, 80, 0, 0.5)).toBe(true);
  });

  it('honours the clearance band around a footprint', () => {
    const state = new State();
    registerSpawnFootprint(state, 0, 0, 1);
    // radius 1 + radius 1 + 0.6 clearance = blocked out to 2.6 m.
    expect(isSpawnAreaFree(state, 2.5, 0, 1)).toBe(false);
    expect(isSpawnAreaFree(state, 2.7, 0, 1)).toBe(true);
  });

  it('ignores non-positive radii and starts empty', () => {
    const state = new State();
    expect(isSpawnAreaFree(state, 0, 0, 1)).toBe(true);
    registerSpawnFootprint(state, 0, 0, 0);
    registerSpawnFootprint(state, 0, 0, -3);
    expect(isSpawnAreaFree(state, 0, 0, 1)).toBe(true);
  });

  it('forgets everything on clear', () => {
    const state = new State();
    registerSpawnFootprint(state, 10, 10, 4);
    expect(isSpawnAreaFree(state, 10, 10, 1)).toBe(false);
    clearSpawnOccupancy(state);
    expect(isSpawnAreaFree(state, 10, 10, 1)).toBe(true);
  });
});
