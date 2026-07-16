import { describe, expect, it } from 'bun:test';
import type { SpawnGroupSpec } from '../../../src/plugins/spawner/types';

/**
 * Documents TerrainSpawnSystem contract: non-empty clusterCenters skips
 * random hub generation (see systems.ts). Pure shape check — placement
 * integration covered via vegetation smart planner.
 */
describe('SpawnGroupSpec.clusterCenters', () => {
  it('optional field accepts precomputed hubs', () => {
    const hubs: Array<[number, number]> = [
      [0, 0],
      [5, -3],
    ];
    const spec = {
      clusterCount: 0,
      clusterRadius: 2.2,
      clusterCenters: hubs,
    } as Pick<
      SpawnGroupSpec,
      'clusterCount' | 'clusterRadius' | 'clusterCenters'
    >;
    expect(spec.clusterCenters!.length).toBe(2);
    expect(spec.clusterCenters![1]).toEqual([5, -3]);
  });
});
