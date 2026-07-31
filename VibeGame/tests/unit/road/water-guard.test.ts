import { describe, expect, it } from 'bun:test';
import { State } from '../../../src/core/ecs/state';
import {
  registerWaterBody,
  type LakeWaterBody,
  type RiverWaterBody,
} from '../../../src/plugins/water/registry';
import {
  corridorOverlapsWater,
  waterNoRaiseFloorLocal,
  waterPreserveZonesLocal,
} from '../../../src/plugins/road/water-guard';

describe('corridorOverlapsWater', () => {
  it('detects a lake whose carve disc reaches the road path', () => {
    const lake: LakeWaterBody = {
      kind: 'lake',
      x: 0,
      z: 0,
      radius: 20,
      shoreRadius: 16,
      carveRadius: 28,
      waterY: 40,
    };
    expect(
      corridorOverlapsWater([-40, 0, 40, 0], 4, lake, { x: 0, z: 0 })
    ).toBe(true);
  });

  it('ignores a lake far from the corridor', () => {
    const lake: LakeWaterBody = {
      kind: 'lake',
      x: 200,
      z: 200,
      radius: 14,
      shoreRadius: 11,
      carveRadius: 20,
      waterY: 40,
    };
    expect(
      corridorOverlapsWater([-40, 0, 40, 0], 4, lake, { x: 0, z: 0 })
    ).toBe(false);
  });

  it('detects a river ribbon overlapping the road', () => {
    const river: RiverWaterBody = {
      kind: 'river',
      path: [
        [0, -30],
        [0, 30],
      ],
      width: 8,
      carveWidth: 12,
      waterY: 30,
    };
    expect(
      corridorOverlapsWater([-20, 0, 20, 0], 4, river, { x: 0, z: 0 })
    ).toBe(true);
  });
});

describe('waterNoRaiseFloorLocal', () => {
  it('returns the lowest overlapping waterY in field-local space', () => {
    const state = new State();
    registerWaterBody(state, {
      kind: 'lake',
      x: 10,
      z: 0,
      radius: 20,
      shoreRadius: 16,
      carveRadius: 28,
      waterY: 42,
    });
    registerWaterBody(state, {
      kind: 'lake',
      x: -10,
      z: 0,
      radius: 20,
      shoreRadius: 16,
      carveRadius: 28,
      waterY: 38,
    });
    const floor = waterNoRaiseFloorLocal(
      state,
      [-40, 0, 40, 0],
      6,
      { x: 0, z: 0 },
      5
    );
    // localY = waterY - baseY → 38 - 5 = 33 (lowest).
    expect(floor).toBeCloseTo(33, 5);
  });

  it('returns undefined when no water overlaps the corridor', () => {
    const state = new State();
    registerWaterBody(state, {
      kind: 'lake',
      x: 500,
      z: 500,
      radius: 10,
      shoreRadius: 8,
      waterY: 40,
    });
    expect(
      waterNoRaiseFloorLocal(state, [-40, 0, 40, 0], 6, { x: 0, z: 0 }, 0)
    ).toBeUndefined();
  });

  it('waterPreserveZonesLocal emits shore discs (not full carve) for lakes', () => {
    const state = new State();
    registerWaterBody(state, {
      kind: 'lake',
      x: 0,
      z: 0,
      radius: 20,
      shoreRadius: 16,
      carveRadius: 28,
      waterY: 40,
    });
    const zones = waterPreserveZonesLocal(
      state,
      [-40, 0, 40, 0],
      6,
      { x: 0, z: 0 },
      0
    );
    expect(zones.noRaiseBelowY).toBe(40);
    // Shore only — banks stay stampable for bridge/pad abutments.
    expect(zones.discs).toEqual([{ x: 0, z: 0, r: 16 }]);
    expect(zones.ribbons).toEqual([]);
  });
});
