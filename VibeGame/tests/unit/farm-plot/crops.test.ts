import { beforeEach, describe, expect, it } from 'bun:test';
import {
  FarmGrid,
  FarmPlotPlugin,
  FarmTileStates,
  State,
  advanceFarmDay,
  clearTile,
  createFarmGrid,
  getTileState,
  harvestTile,
  onFarmTileChanged,
  plantSeed,
  tillTile,
  waterTile,
} from 'vibegame';
import { getDataRegistry } from '../../../src/plugins/rpg-core/registry';
import { FarmGridSetupSystem } from '../../../src/plugins/farm-plot/systems';
import { getFarmGridData } from '../../../src/plugins/farm-plot/store';

const TURNIP = `
crop:
  turnip:
    seasons: [0, 1]
    seedItemId: turnip_seed
    yieldItemId: turnip
    yieldMin: 1
    yieldMax: 3
    daysPerStage: [1, 2]
    regrowDays: 0
    witherAfterDays: 2
    stageHeights: [0.1, 0.3, 0.5]
    color: 0x88cc44
`;

const TURNIP_TOMATO = `
crop:
  turnip:
    seasons: [0, 1]
    seedItemId: turnip_seed
    yieldItemId: turnip
    yieldMin: 1
    yieldMax: 3
    daysPerStage: [1, 2]
    regrowDays: 0
    witherAfterDays: 2
    stageHeights: [0.1, 0.3, 0.5]
    color: 0x88cc44
  tomato:
    seasons: [0, 1, 2]
    seedItemId: tomato_seed
    yieldItemId: tomato
    yieldMin: 2
    yieldMax: 2
    daysPerStage: [1, 2, 2]
    regrowDays: 2
    witherAfterDays: 0
    stageHeights: [0.15, 0.35, 0.55, 0.75]
    color: 0xcc4444
`;

function boot(yaml: string): { state: State; grid: number } {
  const state = new State();
  state.registerPlugin(FarmPlotPlugin);
  getDataRegistry(state).loadYaml(yaml);
  const grid = createFarmGrid(state, {
    atX: 0,
    atZ: 0,
    cols: 4,
    rows: 3,
    baseY: 12,
  });
  FarmGridSetupSystem.update!(state);
  return { state, grid };
}

describe('farm-plot crop lifecycle', () => {
  let state: State;
  let grid: number;

  beforeEach(() => {
    ({ state, grid } = boot(TURNIP_TOMATO));
  });

  it('setup interns crop ids alphabetically and marks the grid ready', () => {
    const data = getFarmGridData(state, grid)!;
    expect(data.ready).toBe(true);
    expect(data.cropIds).toEqual(['tomato', 'turnip']);
    expect(FarmGrid.baseY[grid]).toBe(12);
  });

  it('mutators refuse before setup has run', () => {
    const fresh = new State();
    fresh.registerPlugin(FarmPlotPlugin);
    const g = createFarmGrid(fresh, { atX: 0, atZ: 0, cols: 2, rows: 2 });
    expect(tillTile(fresh, g, 0, 0)).toBe(false);
    expect(advanceFarmDay(fresh, g)).toBeNull();
  });

  it('till→plant→water→grow→ready→harvest, the full loop', () => {
    expect(tillTile(state, grid, 1, 1)).toBe(true);
    expect(plantSeed(state, grid, 1, 1, 'turnip')).toBe(true);

    // turnip: daysPerStage [1,2] → 3 watered days to ripen.
    waterTile(state, grid, 1, 1);
    advanceFarmDay(state, grid);
    expect(getTileState(state, grid, 1, 1)!.state).toBe(FarmTileStates.Growing);
    // Day 1 filled stage 0's quota → already showing stage 1.
    expect(getTileState(state, grid, 1, 1)!.stage).toBe(1);

    waterTile(state, grid, 1, 1);
    advanceFarmDay(state, grid);
    // growthDays 2 of 3: still inside stage 1's two-day window.
    expect(getTileState(state, grid, 1, 1)!.stage).toBe(1);

    waterTile(state, grid, 1, 1);
    const ripened = advanceFarmDay(state, grid);
    // `grown` counts tiles that earned a growth day THIS sleep — one tile.
    expect(ripened).toEqual({ grown: 1, ripened: 1, withered: 0 });
    const tile = getTileState(state, grid, 1, 1)!;
    expect(tile.state).toBe(FarmTileStates.Ready);
    expect(tile.stage).toBe(2);

    const rngAlwaysZero = () => 0;
    const yieldResult = harvestTile(state, grid, 1, 1, rngAlwaysZero);
    expect(yieldResult).toEqual({
      cropId: 'turnip',
      itemId: 'turnip',
      count: 1,
      regrown: false,
    });
    const after = getTileState(state, grid, 1, 1)!;
    expect(after.state).toBe(FarmTileStates.Tilled);
    expect(after.cropId).toBeNull();
  });

  it('unwatered crops do not grow and accumulate dry days', () => {
    tillTile(state, grid, 0, 0);
    plantSeed(state, grid, 0, 0, 'turnip');
    const report = advanceFarmDay(state, grid);
    expect(report!.grown).toBe(0);
    const tile = getTileState(state, grid, 0, 0)!;
    expect(tile.growthDays).toBe(0);
    expect(tile.dryDays).toBe(1);
  });

  it('crops wither after witherAfterDays dry days and clearTile resets', () => {
    tillTile(state, grid, 0, 0);
    plantSeed(state, grid, 0, 0, 'turnip'); // witherAfterDays: 2
    advanceFarmDay(state, grid);
    const report = advanceFarmDay(state, grid);
    expect(report!.withered).toBe(1);
    expect(getTileState(state, grid, 0, 0)!.state).toBe(
      FarmTileStates.Withered
    );
    // Withered crops cannot be harvested — only cleared.
    expect(harvestTile(state, grid, 0, 0)).toBeNull();
    expect(clearTile(state, grid, 0, 0)).toBe(true);
    expect(getTileState(state, grid, 0, 0)!.state).toBe(FarmTileStates.Empty);
  });

  it('witherAfterDays 0 never withers (tomato)', () => {
    tillTile(state, grid, 2, 2);
    plantSeed(state, grid, 2, 2, 'tomato');
    for (let day = 0; day < 9; day++) advanceFarmDay(state, grid);
    const tile = getTileState(state, grid, 2, 2)!;
    expect(tile.state).toBe(FarmTileStates.Growing);
    expect(tile.dryDays).toBe(9);
  });

  it('regrow crops keep growing after harvest with fewer days left', () => {
    tillTile(state, grid, 3, 0);
    plantSeed(state, grid, 3, 0, 'tomato'); // total 5 days, regrow 2
    for (let day = 0; day < 5; day++) {
      waterTile(state, grid, 3, 0);
      advanceFarmDay(state, grid);
    }
    expect(getTileState(state, grid, 3, 0)!.state).toBe(FarmTileStates.Ready);

    const result = harvestTile(state, grid, 3, 0, () => 0.99);
    expect(result).toEqual({
      cropId: 'tomato',
      itemId: 'tomato',
      count: 2,
      regrown: true,
    });
    const after = getTileState(state, grid, 3, 0)!;
    expect(after.state).toBe(FarmTileStates.Growing);
    expect(after.growthDays).toBe(3); // 5 total − 2 regrow
    expect(after.regrowCount).toBe(1);
  });

  it('planting and tiling preconditions', () => {
    expect(plantSeed(state, grid, 0, 0, 'turnip')).toBe(false); // untilled
    expect(tillTile(state, grid, 0, 0)).toBe(true);
    expect(tillTile(state, grid, 0, 0)).toBe(false); // already tilled
    expect(plantSeed(state, grid, 0, 0, 'kale')).toBe(false); // unknown crop
    expect(plantSeed(state, grid, 0, 0, 'turnip')).toBe(true);
    expect(plantSeed(state, grid, 0, 0, 'tomato')).toBe(false); // occupied
    expect(waterTile(state, grid, 0, 0)).toBe(true);
  });

  it('watering is refused on empty and withered tiles', () => {
    expect(waterTile(state, grid, 0, 0)).toBe(false);
    tillTile(state, grid, 0, 0);
    expect(waterTile(state, grid, 0, 0)).toBe(true);
  });

  it('out-of-bounds coordinates always fail', () => {
    expect(tillTile(state, grid, -1, 0)).toBe(false);
    expect(tillTile(state, grid, 4, 0)).toBe(false);
    expect(getTileState(state, grid, 0, 9)).toBeNull();
  });

  it('version bumps on every mutation', () => {
    const v0 = FarmGrid.version[grid];
    tillTile(state, grid, 0, 0);
    const v1 = FarmGrid.version[grid];
    expect(v1).toBeGreaterThan(v0);
    advanceFarmDay(state, grid);
    expect(FarmGrid.version[grid]).toBeGreaterThan(v1);
  });

  it('onFarmTileChanged fires for mutations with the tile index', () => {
    const seen: Array<[number, number]> = [];
    const off = onFarmTileChanged(state, (eid, idx) => seen.push([eid, idx]));
    tillTile(state, grid, 2, 1); // index = 1*4 + 2 = 6
    off();
    tillTile(state, grid, 0, 0);
    expect(seen).toEqual([[grid, 6]]);
  });

  it('day report counts across multiple tiles', () => {
    tillTile(state, grid, 0, 0);
    plantSeed(state, grid, 0, 0, 'turnip');
    tillTile(state, grid, 1, 0);
    plantSeed(state, grid, 1, 0, 'turnip');
    waterTile(state, grid, 0, 0); // only one watered
    const report = advanceFarmDay(state, grid);
    expect(report).toEqual({ grown: 1, ripened: 0, withered: 0 });
  });

  it('empty registry leaves the grid ready but unplanted', () => {
    const bare = new State();
    bare.registerPlugin(FarmPlotPlugin);
    const g = createFarmGrid(bare, { atX: 0, atZ: 0, cols: 2, rows: 2 });
    FarmGridSetupSystem.update!(bare);
    const data = getFarmGridData(bare, g)!;
    expect(data.ready).toBe(true);
    expect(data.cropIds).toEqual([]);
  });

  it('a single-crop registry still sorts and interns', () => {
    const solo = boot(TURNIP);
    expect(getFarmGridData(solo.state, solo.grid)!.cropIds).toEqual(['turnip']);
  });
});
