import { beforeEach, describe, expect, it } from 'bun:test';
import {
  FarmPlotPlugin,
  FarmTileStates,
  State,
  advanceFarmDay,
  createFarmGrid,
  deserializeFarmGrid,
  getTileState,
  plantSeed,
  serializeFarmGrid,
  tillTile,
  waterTile,
} from 'vibegame';
import { getDataRegistry } from '../../../src/plugins/rpg-core/registry';
import { FarmGridSetupSystem } from '../../../src/plugins/farm-plot/systems';

const APPLE_BEET = `
crop:
  apple:
    seedItemId: apple_seed
    yieldItemId: apple
    daysPerStage: [1, 1]
    stageHeights: [0.2, 0.4, 0.6]
  beet:
    seedItemId: beet_seed
    yieldItemId: beet
    daysPerStage: [2]
    stageHeights: [0.2, 0.5]
`;

// Same crops plus a new one that sorts BETWEEN them — every saved index for
// beet shifts by one. This is the "mods added crops" save-compat scenario.
const APPLE_CHERRY_BEET = `
crop:
  apple:
    seedItemId: apple_seed
    yieldItemId: apple
    daysPerStage: [1, 1]
    stageHeights: [0.2, 0.4, 0.6]
  cherry:
    seedItemId: cherry_seed
    yieldItemId: cherry
    daysPerStage: [1]
    stageHeights: [0.2, 0.4]
  beet:
    seedItemId: beet_seed
    yieldItemId: beet
    daysPerStage: [2]
    stageHeights: [0.2, 0.5]
`;

function boot(yaml: string) {
  const state = new State();
  state.registerPlugin(FarmPlotPlugin);
  getDataRegistry(state).loadYaml(yaml);
  const grid = createFarmGrid(state, {
    atX: 5,
    atZ: -3,
    cols: 3,
    rows: 2,
    baseY: 12,
  });
  FarmGridSetupSystem.update!(state);
  return { state, grid };
}

describe('farm-grid serializer', () => {
  let state: State;
  let grid: number;

  beforeEach(() => {
    ({ state, grid } = boot(APPLE_BEET));
  });

  it('round-trips every tile field through serialize→deserialize', () => {
    tillTile(state, grid, 0, 0);
    plantSeed(state, grid, 0, 0, 'beet');
    waterTile(state, grid, 0, 0);
    tillTile(state, grid, 2, 1);
    plantSeed(state, grid, 2, 1, 'apple');
    // growthDays=1 for the apple (2 days total) — simulate one watered sleep.
    waterTile(state, grid, 2, 1);
    advanceFarmDay(state, grid);

    const save = serializeFarmGrid(state, grid)!;
    expect(save.cropIds).toEqual(['apple', 'beet']);
    expect(save.cols).toBe(3);
    expect(save.rows).toBe(2);

    // Fresh world, same registry.
    const next = boot(APPLE_BEET);
    deserializeFarmGrid(next.state, next.grid, save);
    const beetTile = getTileState(next.state, next.grid, 0, 0)!;
    expect(beetTile.state).toBe(FarmTileStates.Growing);
    expect(beetTile.cropId).toBe('beet');
    expect(beetTile.wateredToday).toBe(false); // the day rolled over
    const appleTile = getTileState(next.state, next.grid, 2, 1)!;
    expect(appleTile.cropId).toBe('apple');
    expect(appleTile.growthDays).toBe(1);
    expect(getTileState(next.state, next.grid, 1, 0)!.state).toBe(
      FarmTileStates.Empty
    );
  });

  it('remaps crop indices when the alphabet grows between save and load', () => {
    tillTile(state, grid, 1, 0);
    plantSeed(state, grid, 1, 0, 'beet'); // index 1 in [apple, beet]
    const save = serializeFarmGrid(state, grid)!;

    // beet is now index 2 in [apple, cherry, beet].
    const next = boot(APPLE_CHERRY_BEET);
    deserializeFarmGrid(next.state, next.grid, save);
    expect(getTileState(next.state, next.grid, 1, 0)!.cropId).toBe('beet');
  });

  it('a crop missing from the registry degrades its tile without corrupting others', () => {
    tillTile(state, grid, 0, 0);
    plantSeed(state, grid, 0, 0, 'beet');
    tillTile(state, grid, 1, 0);
    plantSeed(state, grid, 1, 0, 'apple');
    const save = serializeFarmGrid(state, grid)!;

    // Load into a registry where beet no longer exists.
    const beetless = `
crop:
  apple:
    seedItemId: apple_seed
    yieldItemId: apple
    daysPerStage: [1, 1]
    stageHeights: [0.2, 0.4, 0.6]
`;
    const next = boot(beetless);
    deserializeFarmGrid(next.state, next.grid, save);
    expect(getTileState(next.state, next.grid, 0, 0)!.state).toBe(
      FarmTileStates.Empty
    );
    expect(getTileState(next.state, next.grid, 1, 0)!.cropId).toBe('apple');
  });

  it('rejects payloads whose shape does not match the grid', () => {
    const save = serializeFarmGrid(state, grid)!;
    expect(deserializeFarmGrid(state, grid, { v: 2 })).toBe(false);
    expect(
      deserializeFarmGrid(state, grid, { ...save, tiles: 'not base64!!' })
    ).toBe(false);
    const wrongTiles = { ...save, cols: save.cols + 1 };
    expect(deserializeFarmGrid(state, grid, wrongTiles)).toBe(false);
  });
});
