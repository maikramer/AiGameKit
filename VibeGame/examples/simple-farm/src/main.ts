import {
  configure,
  getBuilder,
  getDataRegistry,
  registerDebugAction,
  registerEntityScripts,
  releaseRuntimeGpuResources,
  resetBuilder,
  setKTX2TranscoderPath,
  t,
  withPlugin,
  withPlugins,
  withSystem,
  // plugins
  DebugPlugin,
  DayCyclePlugin,
  FarmPlotPlugin,
  I18nPlugin,
  IsometricCameraPlugin,
  LoadingPlugin,
  ParticlesPlugin,
  ProfilerPlugin,
  RpgPlugins,
  SaveLoadPlugin,
  SpawnGatePlugin,
  // farm api
  clearTile,
  getFacingCell,
  getFarmGrid,
  getTileState,
  harvestTile,
  plantSeed,
  tillTile,
  waterTile,
  // loading screen
  mountLoadingScreen,
  setLoadingScreenLocale,
} from 'vibegame';
import type { State } from 'vibegame';

setKTX2TranscoderPath('/libs/basis/');

import { detectLocale, initI18n } from '../../shared/src/i18n';
import { setupHmrGuard } from '../../shared/src/hmr';
import { wireOptions } from '../../shared/src/options';
import { registerProfilerDebug } from '../../shared/src/profiler';
import { teleportEntity } from '../../shared/src/physics';
import { showToast } from '../../shared/src/ui';
import { FARM_DICTIONARY } from './data/i18n';
import { FarmGameSystem, initFarmGame } from './game/farm-game';
import { registerSleep, sleepAtBed } from './game/sleep';
import { registerShop } from './game/shop';
import { registerHarvest } from './game/harvest';
import { FarmResourceKindsPlugin } from './game/resource-kinds';

const SAVE_KEY = 'simple-farm-save';

/** Crop/item data for the farm — fetched, not bundled: same pipeline the
 *  <RpgData> recipe uses in bun, inert-friendly in the browser. */
async function loadFarmData(state: State): Promise<void> {
  const registry = getDataRegistry(state);
  for (const file of ['crops', 'items', 'tools']) {
    const response = await fetch(`/data/${file}.yaml`);
    if (!response.ok)
      throw new Error(`/data/${file}.yaml: HTTP ${response.status}`);
    registry.loadYaml(await response.text());
  }
}

/** Debug surface for the field: REPL actions over the facing tile. */
function registerFarmDebug(state: State): void {
  // QA teleport onto the field: __VIBEGAME__.debug.callAction('tp', 2, 12.5, 20)
  registerDebugAction(state, 'tp', (x: number, y: number, z: number) => {
    const hero = state.getEntityByName('player') ?? 0;
    if (!hero) return -1;
    teleportEntity(state, hero, x, y, z);
    return hero;
  });

  const facing = () => {
    const grid = getFarmGrid(state);
    const player = state.getEntityByName('player');
    if (!grid || !player) return null;
    const cell = getFacingCell(state, grid.eid, player);
    return cell ? { ...cell, grid: grid.eid } : null;
  };

  registerDebugAction(state, 'till-facing', () => {
    const f = facing();
    return f ? tillTile(state, f.grid, f.col, f.row) : false;
  });
  registerDebugAction(state, 'water-facing', () => {
    const f = facing();
    return f ? waterTile(state, f.grid, f.col, f.row) : false;
  });
  registerDebugAction(state, 'plant-facing', (cropId: string = 'turnip') => {
    const f = facing();
    return f ? plantSeed(state, f.grid, f.col, f.row, cropId) : false;
  });
  registerDebugAction(state, 'harvest-facing', () => {
    const f = facing();
    if (!f) return null;
    return harvestTile(state, f.grid, f.col, f.row);
  });
  registerDebugAction(state, 'clear-facing', () => {
    const f = facing();
    return f ? clearTile(state, f.grid, f.col, f.row) : false;
  });
  registerDebugAction(state, 'tile-facing', () => {
    const f = facing();
    return f ? getTileState(state, f.grid, f.col, f.row) : null;
  });
  // QA/smoke: sleep to next morning without walking to the bed.
  registerDebugAction(state, 'sleep', () => {
    sleepAtBed(state);
    return true;
  });
  // QA/smoke: read or till a fixed cell (the facing cell can drift as
  // physics settles after a teleport).
  registerDebugAction(state, 'tile-at', (col: number, row: number) => {
    const grid = getFarmGrid(state);
    return grid ? getTileState(state, grid.eid, col, row) : null;
  });
  registerDebugAction(state, 'till-at', (col: number, row: number) => {
    const grid = getFarmGrid(state);
    return grid ? tillTile(state, grid.eid, col, row) : false;
  });
}

/**
 * Single-flight boot. Vite HMR (and a double module evaluation in dev) would
 * otherwise build a second runtime on top of the live one.
 */
let bootstrapPromise: Promise<void> | null = null;

async function runBootstrap(): Promise<void> {
  resetBuilder();

  const bootLang = detectLocale();
  setLoadingScreenLocale(bootLang);
  mountLoadingScreen({
    title: 'Simple Farm',
    subtitle: bootLang === 'pt' ? 'A acordar o vale…' : 'Waking the valley…',
  });

  withPlugin(LoadingPlugin);
  // Opt-in camera rig: not in DefaultPlugins, so a scene only ever gets one.
  withPlugin(IsometricCameraPlugin);
  // Farm tile grid: <FarmPlot> + crop state machine + instanced field render.
  withPlugin(FarmPlotPlugin);
  // Day/night calendar driving the sky + a <Clock> HUD widget.
  withPlugin(DayCyclePlugin);
  // RPG bundle: inventory / vault / economy / progression / resource nodes.
  // The farming layer builds on top of it from phase 6.
  withPlugins(...RpgPlugins);
  // Adds `food` to the resource-node kind enum. Must come AFTER the RPG bundle:
  // config.enums assigns the whole `kind` mapping, so whoever registers last wins.
  withPlugin(FarmResourceKindsPlugin);
  withPlugin(SpawnGatePlugin);
  withPlugin(ParticlesPlugin);
  withPlugin(SaveLoadPlugin);
  // Game layer: [J] dispatch, stamina, hotbar sync (src/game/).
  withSystem(FarmGameSystem);
  withPlugin(I18nPlugin);
  withPlugin(DebugPlugin);
  withPlugin(ProfilerPlugin);

  configure({ canvas: '#game-canvas' });

  const runtime = await getBuilder().build();
  const state = runtime.getState();

  // Crop defs must be interned before the first setup pass — FarmGridSetupSystem
  // reads the registry while the world builds, so this precedes runtime.start().
  await loadFarmData(state);

  registerEntityScripts(state, import.meta.glob('./scripts/**/*.ts'));

  initI18n(state, FARM_DICTIONARY);
  wireOptions(state, {
    saveKey: SAVE_KEY,
    onSave: () => showToast(t(state, 'hud.saved'), { durationMs: 1400 }),
    onLoad: (restored) => {
      if (restored) showToast(t(state, 'hud.loaded'), { durationMs: 1400 });
    },
  });
  registerProfilerDebug(state);
  registerFarmDebug(state);

  await runtime.start();

  // Game-layer wiring runs after start(): the bed/stall prompts resolve
  // entities by name, and names only exist once the world is live.
  initFarmGame(state);
  registerSleep(state);
  registerShop(state);
  registerHarvest(state);
}

function bootstrap(): Promise<void> {
  bootstrapPromise ??= runBootstrap();
  return bootstrapPromise;
}

void bootstrap().catch((err) => {
  console.error('[simple-farm] boot failed:', err);
  mountLoadingScreen({
    title: 'Simple Farm',
    subtitle: `Boot failed: ${err instanceof Error ? err.message : String(err)}`,
  });
});

setupHmrGuard(() => {
  releaseRuntimeGpuResources();
});
