import { beforeEach, describe, expect, it } from 'bun:test';
import { WorldTransform } from 'vibegame';
import { pickNearestLightSlots } from '../../../src/plugins/rendering/systems';

/**
 * Slot assignment for `PointLight` entities. Slots used to be
 * first-come-first-served, so a dozen lanterns near the origin claimed every
 * slot at boot and every torch/brazier the player later walked up to stayed
 * dark — while warning once per entity per frame.
 */
function placeAt(eid: number, x: number, y: number, z: number): void {
  WorldTransform.posX[eid] = x;
  WorldTransform.posY[eid] = y;
  WorldTransform.posZ[eid] = z;
}

describe('pickNearestLightSlots', () => {
  const entities = [1, 2, 3, 4, 5];

  beforeEach(() => {
    placeAt(1, 0, 0, 0);
    placeAt(2, 10, 0, 0);
    placeAt(3, 20, 0, 0);
    placeAt(4, 30, 0, 0);
    placeAt(5, 40, 0, 0);
  });

  it('keeps the nearest entities to the camera', () => {
    const active = pickNearestLightSlots(entities, new Set(), 0, 0, 0, 2);
    expect([...active].sort()).toEqual([1, 2]);
  });

  it('follows the camera instead of freezing the boot-time winners', () => {
    // Camera walked out to the far cluster: the near lanterns must give up
    // their slots, which is exactly what the old first-come logic never did.
    const active = pickNearestLightSlots(
      entities,
      new Set([1, 2]),
      40,
      0,
      0,
      2
    );
    expect([...active].sort()).toEqual([4, 5]);
  });

  it('gives current holders hysteresis at the boundary', () => {
    // eid 2 sits at x=10 and holds the slot, eid 3 at x=20. At x=15.4 the
    // challenger is already nearer (4.6 vs 5.4) but not by the 20% margin
    // (0.64·5.4² = 18.7 < 4.6² = 21.2), so no swap — that margin is what stops
    // lights blinking while the player walks between two clusters.
    const held = pickNearestLightSlots(entities, new Set([2]), 15.4, 0, 0, 1);
    expect([...held]).toEqual([2]);

    // Past the margin the swap goes through (0.64·7² = 31.4 > 3² = 9).
    const swapped = pickNearestLightSlots(entities, new Set([2]), 17, 0, 0, 1);
    expect([...swapped]).toEqual([3]);
  });

  it('returns every entity when there are fewer than the cap', () => {
    const active = pickNearestLightSlots([7, 8], new Set(), 0, 0, 0, 12);
    expect(active.size).toBe(2);
  });

  it('accepts a Map of holders (the live entity→light map)', () => {
    const holders = new Map<number, unknown>([[5, {}]]);
    const active = pickNearestLightSlots(entities, holders, 40, 0, 0, 1);
    expect([...active]).toEqual([5]);
  });
});
