import { describe, expect, it } from 'bun:test';
import { State } from '../../../src/core';
import { registerWaterBody } from '../../../src/plugins/water/registry';
import {
  bridgeDeckCenterXZ,
  crossingOnRiver,
  riverCrossingAt,
} from '../../../src/plugins/road/river-crossing';
import { bridgeMidXZ } from '../../../src/plugins/road/bridge';

describe('riverCrossingAt', () => {
  it('crossingOnRiver returns centreline hit under the span', () => {
    const body = {
      kind: 'river' as const,
      path: [
        [20, -124],
        [-20, -124],
      ] as const,
      width: 16,
      shoreWidth: 16,
      carveWidth: 28,
      waterY: 33,
    };
    const hit = crossingOnRiver(body, 4, -124, 16);
    expect(hit).not.toBeNull();
    expect(hit!.x).toBeCloseTo(4, 5);
    expect(hit!.z).toBeCloseTo(-124, 5);
    expect(hit!.half).toBe(8);
    expect(hit!.carveHalf).toBe(14);
  });

  it('crossingOnRiver ignores a river far from the span', () => {
    const body = {
      kind: 'river' as const,
      path: [
        [200, 0],
        [240, 0],
      ] as const,
      width: 16,
      waterY: 33,
    };
    expect(crossingOnRiver(body, 4, -124, 16)).toBeNull();
  });

  it('riverCrossingAt / bridgeDeckCenterXZ prefer river mid over Ways mid', () => {
    const state = new State();
    registerWaterBody(state, {
      kind: 'river',
      path: [
        [20, -120],
        [-20, -120],
      ],
      width: 16,
      shoreWidth: 16,
      carveWidth: 28,
      waterY: 33,
    });
    // Ways mid at z=-124, river at z=-120 → deck must snap to river.
    const path = [4, -108, 4, -140];
    expect(bridgeMidXZ(path).z).toBeCloseTo(-124, 5);
    const crossing = riverCrossingAt(state, path);
    expect(crossing).not.toBeNull();
    expect(crossing!.z).toBeCloseTo(-120, 5);
    const center = bridgeDeckCenterXZ(state, path);
    expect(center.z).toBeCloseTo(-120, 5);
    expect(center.x).toBeCloseTo(4, 5);
  });

  it('bridgeDeckCenterXZ falls back to Ways mid without water', () => {
    const state = new State();
    const path = [4, -108, 4, -140];
    expect(bridgeDeckCenterXZ(state, path)).toEqual(bridgeMidXZ(path));
  });
});
