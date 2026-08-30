import { beforeEach, describe, expect, it } from 'bun:test';
import {
  DayCyclePlugin,
  FarmPlotPlugin,
  FarmTileStates,
  GameClock,
  InventoryComponent,
  InventoryEventBridgeSystem,
  InventoryPlugin,
  RpgVaultPlugin,
  SaveLoadPlugin,
  State,
  VaultComponent,
  addItem,
  addResource,
  clearTile,
  createFarmGrid,
  defineQuery,
  getDataRegistry,
  getInventory,
  getResource,
  getTileState,
  loadSnapshot,
  plantSeed,
  saveSnapshot,
  spendResource,
  tillTile,
} from 'aigamekit-vibegame';
import { FarmGridSetupSystem } from '../../../src/plugins/farm-plot/systems';

/**
 * The localStorage blob is the core world snapshot PLUS the serializer
 * registry payload (farm tiles, string-keyed inventories, globals) — this
 * file proves the combined `saveSnapshot`/`loadSnapshot` round-trip restores
 * side-table state that raw component arrays cannot carry.
 */

async function makeState(): Promise<State> {
  const state = new State();
  state.registerPlugin(SaveLoadPlugin);
  state.registerPlugin(RpgVaultPlugin);
  state.registerPlugin(InventoryPlugin);
  state.registerPlugin(FarmPlotPlugin);
  state.registerPlugin(DayCyclePlugin);
  // Plugin initializers register the save serializers; in a runtime the
  // builder runs them, in tests we do it explicitly.
  await state.initializePlugins();
  getDataRegistry(state).register('item', 'turnip', {
    id: 'turnip',
    name: 'Turnip',
    maxStack: 99,
    tags: ['produce'],
  });
  getDataRegistry(state).register('crop', 'turnip', {
    id: 'turnip',
    seasons: [0, 1],
    seedItemId: 'seed_turnip',
    yieldItemId: 'turnip',
    yieldMin: 1,
    yieldMax: 2,
    daysPerStage: [1, 1],
    regrowDays: 0,
    witherAfterDays: 2,
    stageHeights: [0.1, 0.3, 0.5],
    color: 0x9ecf5a,
  });
  return state;
}

const clockQuery = defineQuery([GameClock]);

async function boot() {
  const state = await makeState();
  const player = state.createEntity();
  state.setEntityName('player', player);
  state.addComponent(player, InventoryComponent);
  state.addComponent(player, VaultComponent);
  addResource(state, player, 'gold', 77);

  const grid = createFarmGrid(state, { atX: 0, atZ: 0, cols: 3, rows: 2 });
  FarmGridSetupSystem.update!(state);
  tillTile(state, grid, 1, 1);
  expect(plantSeed(state, grid, 1, 1, 'turnip')).toBeTrue();

  const clock = state.createEntity();
  state.addComponent(clock, GameClock);
  GameClock.day[clock] = 5;
  GameClock.minuteOfDay[clock] = 900;

  return { state, player, grid, clock };
}

describe('saveSnapshot/loadSnapshot — registry payload round-trip', () => {
  let setup: Awaited<ReturnType<typeof boot>>;

  beforeEach(async () => {
    setup = await boot();
  });

  it('restores farm tiles (side arrays absent from raw components)', () => {
    const { state, grid } = setup;
    const bytes = saveSnapshot(state);

    expect(getTileState(state, grid, 1, 1)?.state).toBe(FarmTileStates.Growing);
    expect(clearTile(state, grid, 1, 1)).toBeTrue();
    expect(getTileState(state, grid, 1, 1)?.state).toBe(FarmTileStates.Empty);

    loadSnapshot(state, bytes);
    const restored = getTileState(state, grid, 1, 1);
    expect(restored?.state).toBe(FarmTileStates.Growing);
    expect(restored?.cropId).toBe('turnip');
  });

  it('restores the named player vault + clock through name-first matching', () => {
    const { state, player } = setup;
    const bytes = saveSnapshot(state);

    spendResource(state, player, 'gold', 77);
    expect(getResource(state, player, 'gold')).toBe(0);

    loadSnapshot(state, bytes);

    // restoreSnapshot re-created entities under new eids and re-registered
    // names — resolve through the name, not the stale eid.
    const restoredPlayer = state.getEntityByName('player');
    expect(restoredPlayer).not.toBeNull();
    expect(restoredPlayer).not.toBe(player);
    expect(getResource(state, restoredPlayer!, 'gold')).toBe(77);

    const days = clockQuery(state.world).map((eid) => GameClock.day[eid]);
    expect(days).toContain(5);
  });

  it('restores string-keyed inventory stacks', () => {
    const { state } = setup;
    const player = state.getEntityByName('player')!;
    addItem(state, player, 'turnip', 3);
    InventoryEventBridgeSystem.update!(state);
    const bytes = saveSnapshot(state);

    loadSnapshot(state, bytes);
    // The restored player is a NEW entity: its stacks can only have arrived
    // through the registry snapshot (strings), not raw component arrays.
    const restoredPlayer = state.getEntityByName('player')!;
    expect(restoredPlayer).not.toBe(player);
    expect(getInventory(state, restoredPlayer)).toEqual([
      { itemId: 'turnip', qty: 3 },
    ]);
  });
});
