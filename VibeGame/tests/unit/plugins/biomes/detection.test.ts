import { describe, expect, it } from 'bun:test';
import type { State } from '../../../../src/core';
import {
  findBiomeRegionAt,
  getBiomeRegions,
  type BiomeRegionInfo,
} from '../../../../src/plugins/biomes/parser';
import {
  BIOME_BLEND_DURATION,
  NO_BIOME,
  advanceBlend,
} from '../../../../src/plugins/biomes/systems';

function makeState(): State {
  return {} as unknown as State;
}

function region(
  entity: number,
  id: string,
  vertices: number[][]
): BiomeRegionInfo {
  let minX = Infinity;
  let minZ = Infinity;
  let maxX = -Infinity;
  let maxZ = -Infinity;
  for (const [x, z] of vertices) {
    if (x < minX) minX = x;
    if (z < minZ) minZ = z;
    if (x > maxX) maxX = x;
    if (z > maxZ) maxZ = z;
  }
  return { entity, id, vertices, minX, minZ, maxX, maxZ };
}

describe('findBiomeRegionAt (AABB + point-in-polygon)', () => {
  it('detects the region when the player is inside it', () => {
    const state = makeState();
    getBiomeRegions(state).push(
      region(1, 'vale-square', [
        [-10, -10],
        [10, -10],
        [10, 10],
        [-10, 10],
      ])
    );
    const hit = findBiomeRegionAt(state, 0, 0);
    expect(hit).not.toBeNull();
    expect(hit?.entity).toBe(1);
    expect(hit?.id).toBe('vale-square');
  });

  it('returns null when the player is outside every region', () => {
    const state = makeState();
    getBiomeRegions(state).push(
      region(1, 'vale-square', [
        [-10, -10],
        [10, -10],
        [10, 10],
        [-10, 10],
      ])
    );
    expect(findBiomeRegionAt(state, 500, 500)).toBeNull();
  });

  it('returns null when the player is inside the AABB but outside the polygon', () => {
    const state = makeState();
    getBiomeRegions(state).push(
      region(2, 'l-shape', [
        [0, 0],
        [20, 0],
        [20, 10],
        [10, 10],
        [10, 20],
        [0, 20],
      ])
    );
    expect(findBiomeRegionAt(state, 15, 15)).toBeNull();
    expect(findBiomeRegionAt(state, 5, 5)?.entity).toBe(2);
  });

  it('returns null when no regions are registered', () => {
    expect(findBiomeRegionAt(makeState(), 0, 0)).toBeNull();
  });
});

describe('advanceBlend (blend interpolation)', () => {
  it('advances proportionally to dt', () => {
    const dt = BIOME_BLEND_DURATION / 2;
    expect(advanceBlend(0, dt, BIOME_BLEND_DURATION)).toBeCloseTo(0.5, 5);
  });

  it('clamps at 1 when the blend completes', () => {
    expect(
      advanceBlend(0.5, BIOME_BLEND_DURATION / 2, BIOME_BLEND_DURATION)
    ).toBe(1);
  });

  it('clamps at 1 on overshoot (large dt)', () => {
    expect(advanceBlend(0.9, 1, BIOME_BLEND_DURATION)).toBe(1);
  });

  it('jumps straight to 1 when duration is zero', () => {
    expect(advanceBlend(0, 0.016, 0)).toBe(1);
  });

  it('NO_BIOME sentinel is the NULL entity (vale default)', () => {
    expect(NO_BIOME).toBeGreaterThan(0);
    expect(Number.isFinite(NO_BIOME)).toBe(true);
  });
});
