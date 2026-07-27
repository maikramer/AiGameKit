import { describe, expect, it } from 'bun:test';
import { State } from '../../../src/core';
import { Transform, TransformsPlugin } from '../../../src/plugins/transforms';
import { TerrainSpawned } from '../../../src/plugins/spawner/components';
import { terrainSpawnedWorldY } from '../../../src/plugins/spawner/terrain-spawned-y';

describe('terrainSpawnedWorldY (no slope sink)', () => {
  it('returns surface Y plus foot yOffset only', () => {
    const state = new State();
    state.registerPlugin(TransformsPlugin);
    const eid = state.createEntity();
    state.addComponent(eid, Transform);
    state.addComponent(eid, TerrainSpawned);
    TerrainSpawned.yOffset[eid] = 0.25;
    TerrainSpawned.surfaceEpsilon[eid] = 0.75;
    // Without terrain sample the helper returns null.
    expect(terrainSpawnedWorldY(state, eid, 0, 0)).toBeNull();
  });
});
