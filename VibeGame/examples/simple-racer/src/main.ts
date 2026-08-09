/**
 * simple-racer — an arcade racer built on the VibeGame racing plugin.
 *
 * The scene (circuit, cars, camera, HUD) is authored declaratively in
 * index.html; this file only does the three things XML cannot:
 *
 *  1. injects the circuit node list from `src/track.ts` before the parse,
 *  2. registers the sound bank and holds the race on the grid until the kart
 *     models have actually streamed in,
 *  3. wires restart (R) and the music layer.
 */
import * as GAME from 'vibegame';
import { registerGameSounds, preloadGameSounds } from './game/sounds';
import {
  TRACK_ELEVATION,
  TRACK_NODES,
  centerlineAttribute,
  sectionsAttribute,
  widthsAttribute,
} from './track';
import { TrackPropSpawnSystem, resetTrackDressing } from './game/track-props';
import { setupHmrGuard } from '../../shared/src/hmr';
import { initI18n, detectLocale } from '../../shared/src/i18n';
import { wireOptions } from '../../shared/src/options';
import { registerProfilerDebug } from '../../shared/src/profiler';
import { showToast } from '../../shared/src/ui';

/** Fill in the `<RaceTrack>` geometry attributes before the engine parses it. */
function injectTrack(): void {
  const el = document.querySelector('RaceTrack');
  if (!el) return;
  el.setAttribute('centerline', centerlineAttribute());
  el.setAttribute('widths', widthsAttribute());
  el.setAttribute('sections', sectionsAttribute());

  // Fill the road bed `<Road path>` from the same spline (once the engine
  // builds it later) so the carve follows the circuit exactly. The spline
  // needs the centerline attributes set above, so this runs after. Heights
  // carry the same TRACK_ELEVATION as the centerline so the bed carve and
  // the driving surface stay in the same frame.
  const spline = new GAME.TrackSpline(
    TRACK_NODES.map((n) => ({
      x: n.x,
      y: n.y + TRACK_ELEVATION,
      z: n.z,
      width: n.width,
    })),
    { closed: true, step: 6 }
  );
  const path: number[] = [];
  for (let s = 0; s < spline.length; s += 6) {
    const f = spline.sampleAt(s);
    path.push(Number(f.x.toFixed(1)), Number(f.z.toFixed(1)));
  }
  const f0 = spline.sampleAt(0);
  path.push(Number(f0.x.toFixed(1)), Number(f0.z.toFixed(1)));
  document.querySelector('Road')?.setAttribute('path', path.join(' '));
}

/**
 * Keep the cars on the grid until the vehicle GLBs are in memory. Without this
 * the countdown runs while the karts are still invisible and the player's first
 * corner happens before they can see their own car.
 */
async function preloadVehicles(): Promise<void> {
  const urls = [
    '/assets/meshes/vehicles/kart_hero_lod0.glb',
    '/assets/meshes/vehicles/kart_rival_lod0.glb',
  ];
  const loader = GAME.createGLTFLoader();
  // (KTX2 is already wired by the racing visual system on its first frame.)
  await Promise.all(urls.map((url) => loader.loadAsync(url).catch(() => null)));
}

export type RaceMode = 'race' | 'time-trial';

/**
 * Apply the chosen mode to the scene DOM *before* the engine parses it.
 *
 * - Race: keep the `<AiVehicle>` rivals; disable checkpoint respawns (a
 *   full grid races to the flag, off-track is the driver's problem).
 * - Time Trial: drop the rivals; enable checkpoint respawn so a crash costs
 *   time, not the race.
 */
export function applyMode(mode: RaceMode): void {
  const track = document.querySelector('RaceTrack');
  if (mode === 'time-trial') {
    // Solo against the clock: drop the rivals, enable checkpoint respawn so
    // a crash costs time, not the race.
    for (const rival of document.querySelectorAll('AiVehicle')) {
      rival.remove();
    }
    track?.setAttribute('checkpoint-count', '8');
  } else {
    // Full grid: keep the `<AiVehicle>` rivals and disable checkpoint
    // respawns (off-track is the driver's problem in a race).
    track?.removeAttribute('checkpoint-count');
  }
}

/**
 * Wait for the player to pick a mode, then release the grid.
 *
 * Two engine facts make this the right gate rather than a nicety: keyboard
 * input is only recorded once the canvas has focus, and browsers refuse to
 * start audio without a user gesture. Starting the countdown before that click
 * gives the player a race they cannot steer and cannot hear.
 */
function waitForMode(): Promise<RaceMode> {
  const overlay = document.getElementById('loading');
  const canvas = document.getElementById(
    'game-canvas'
  ) as HTMLCanvasElement | null;
  const prompt = overlay?.querySelector('.sub');
  const menu = document.getElementById('mode-menu');
  if (prompt) prompt.textContent = 'Choose a mode';
  overlay?.classList.add('ready');
  menu?.classList.remove('hidden');
  if (!overlay) return Promise.resolve('race');

  return new Promise<RaceMode>((resolve) => {
    const pick = (mode: RaceMode): void => {
      for (const btn of menu?.querySelectorAll('button') ?? []) {
        btn.removeEventListener('click', onPick);
      }
      applyMode(mode);
      overlay.classList.add('hidden');
      // Give the canvas focus so the input plugin starts recording keys. The
      // button click would otherwise keep focus on the button and the input
      // plugin (which gates on `focusedCanvas`) would swallow every key, so
      // steal focus back on the next animation frame (after the click event
      // has fully dispatched).
      canvas?.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
      const stealFocus = (): void => {
        canvas?.focus();
        if (document.activeElement !== canvas) {
          requestAnimationFrame(stealFocus);
        }
      };
      requestAnimationFrame(stealFocus);
      resolve(mode);
    };
    const onPick = (e: Event): void => {
      const mode = (e.currentTarget as HTMLElement).dataset
        .mode as RaceMode | undefined;
      if (mode) pick(mode);
    };
    for (const btn of menu?.querySelectorAll('button') ?? []) {
      btn.addEventListener('click', onPick);
    }
  });
}

/** Music: the race loop while driving, the menu loop over the results screen. */
const BgmSystem = GAME.createMusicLayerDriver({
  resolve: () =>
    GAME.getRaceState().phase === 'finished' ? 'menu' : 'race',
});

// ── Best lap persistence: same player-scoped save serializer pattern as
//    simple-rpg. The race director zeroes bestLapTime when it places cars on
//    the grid, so a small system re-applies the persisted best (and captures
//    new records) — the HUD "Best" line reads bestLapTime[player]. ───────────
const SAVE_KEY = 'simple-racer-save';
const BEST_LAP_KIND = 'racer-best-lap';
let persistedBest = 0;

function registerBestLapSerializer(state: GAME.State): void {
  GAME.registerSaveSerializer(state, BEST_LAP_KIND, {
    serialize: (s, eid) => {
      if (s.getEntityByName('player') !== eid) return null;
      return persistedBest > 0 ? { best: persistedBest } : null;
    },
    deserialize: (s, eid, data) => {
      if (s.getEntityByName('player') !== eid) return;
      const d = data as { best?: number };
      if (typeof d.best === 'number' && d.best > 0) persistedBest = d.best;
    },
  });
}

const BestLapSyncSystem: GAME.System = {
  name: 'BestLapSyncSystem',
  group: 'simulation',
  update(state: GAME.State) {
    const eid = state.getEntityByName('player');
    if (eid === null) return;
    const current = GAME.RaceTracker.bestLapTime[eid];
    if (current > persistedBest) {
      persistedBest = current;
      // New record — same floating-text juice as simple-rpg's combat feedback.
      GAME.spawnFloatingText(state, 'NEW BEST LAP!', {
        x: GAME.Transform.posX[eid],
        y: GAME.Transform.posY[eid] + 2.8,
        z: GAME.Transform.posZ[eid],
        duration: 2.4,
        color: '#7fe0a0',
      });
    } else if (persistedBest > 0 && current < persistedBest) {
      GAME.RaceTracker.bestLapTime[eid] = persistedBest;
    }
  },
};

// ── Next-checkpoint arrow (the same WaypointArrow widget simple-rpg uses for
//    quests): one waypoint per sector, tracked by the player's progress along
//    the spline so it works in both race modes (checkpoint-count is only
//    armed in time-trial). ──────────────────────────────────────────────────
const SECTOR_COUNT = 8;
let checkpointWaypointsReady = false;
let trackLength = 0;
let lastSector = -1;

function registerCheckpointWaypoints(state: GAME.State): boolean {
  const trackEid = GAME.getPrimaryTrackEntity();
  if (trackEid === undefined) return false;
  const spline = GAME.getTrackSpline(trackEid);
  if (!spline) return false;
  trackLength = spline.length;
  for (let k = 0; k < SECTOR_COUNT; k++) {
    const pos = spline.positionAt((k * trackLength) / SECTOR_COUNT, 0);
    GAME.setWaypoint(state, {
      id: `cp-${k}`,
      x: pos.x,
      y: pos.y,
      z: pos.z,
      kind: 'custom',
      glyph: '🏁',
      color: '#e6e8ee',
      label: `CP ${k + 1}`,
    });
  }
  return true;
}

const CheckpointArrowSystem: GAME.System = {
  name: 'CheckpointArrowSystem',
  group: 'simulation',
  update(state: GAME.State) {
    if (!checkpointWaypointsReady) {
      checkpointWaypointsReady = registerCheckpointWaypoints(state);
      if (!checkpointWaypointsReady) return;
    }
    const eid = state.getEntityByName('player');
    if (eid === null) return;
    const sector =
      Math.floor((GAME.Vehicle.trackS[eid] / trackLength) * SECTOR_COUNT) %
      SECTOR_COUNT;
    if (sector === lastSector) return;
    lastSector = sector;
    GAME.setTrackedWaypointId(state, `cp-${(sector + 1) % SECTOR_COUNT}`);
  },
};

// ── World integration (same as simple-rpg): the loading screen is an honest
//    gate — it holds physics until the world is fully ready (assets + terrain
//    colliders + shader warmup) and fades out on its own. The grid stays held
//    until that fade, so the countdown never runs behind the overlay (and the
//    cars never drop through the terrain). ───────────────────────────────────
/** Wait for the loading overlay to fade out (bounded so a load failure can't
 *  soft-lock the race). */
async function waitForLoadingDone(): Promise<void> {
  const deadline = performance.now() + 30000;
  while (document.getElementById('vibegame-loading')) {
    if (performance.now() >= deadline) {
      console.warn('[racer] loading screen never finished — releasing the grid');
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
}

/** Pause menu setup on the first tick: i18n dict + OptionsTab bus wiring. */
let uiSetupDone = false;
const UiSetupSystem: GAME.System = {
  name: 'UiSetupSystem',
  group: 'simulation',
  update(state: GAME.State) {
    if (uiSetupDone) return;
    uiSetupDone = true;
    initI18n(state, {
      en: {
        'modal.tab.options': 'Options',
        'options.restart': '🔁 Restart',
        'options.controls':
          'Drive: WASD / Arrows   Handbrake: Space   Nitro: Shift\n' +
          'Camera: C   Restart: R   Pause: Q',
      },
      pt: {
        'modal.tab.options': 'Opções',
        'options.restart': '🔁 Recomeçar',
        'options.controls':
          'Dirigir: WASD / Setas   Freio de mão: Espaço   Nitro: Shift\n' +
          'Câmera: C   Recomeçar: R   Pausa: Q',
      },
    });
    registerBestLapSerializer(state);
    wireOptions(state, {
      saveKey: SAVE_KEY,
      onSave: () => {
        GAME.playSound('save');
        showToast(GAME.t(state, 'hud.saved'), {
          color: '#7fe0a0',
          borderColor: '#7fe0a0',
          durationMs: 1800,
        });
      },
      onLoad: (restored) => {
        if (!restored) return;
        GAME.playSound('load');
        showToast(GAME.t(state, 'hud.loaded'), {
          color: '#8fb7ff',
          borderColor: '#8fb7ff',
          durationMs: 1800,
        });
      },
      onAction: (id) => {
        if (id === 'restart') {
          GAME.closeModal(state, 'pause');
          GAME.restartRace();
        }
      },
    });
  },
};

let bootstrapPromise: Promise<void> | null = null;

function bootstrap(): Promise<void> {
  // One boot per page load — concurrent re-entry used to race resetBuilder()
  // against a live runtime and leave the tab stuck after Vite full-reload.
  if (bootstrapPromise) return bootstrapPromise;
  bootstrapPromise = runBootstrap();
  return bootstrapPromise;
}

async function runBootstrap(): Promise<void> {
  // Clear any stale builder before registering plugins (same guard as
  // simple-rpg; never dispose a live runtime mid-boot).
  GAME.resetBuilder();

  registerGameSounds();
  preloadGameSounds();
  injectTrack();

  // The BGM plays through the music mixer (`origin: 'music'` resolves the
  // layer name) — register our layers so the mixer doesn't fall back to
  // "explore" (and warn) every frame.
  GAME.registerMusicLayerName('race', 1);
  GAME.registerMusicLayerName('menu', 2);

  GAME.holdRaceOnGrid();
  GAME.withPlugin(GAME.PauseCoordinatorPlugin);
  GAME.withPlugin(GAME.I18nPlugin);
  GAME.withPlugin(GAME.SaveLoadPlugin);
  GAME.withPlugin(GAME.LoadingPlugin);
  // Debug before Profiler (same order as simple-rpg): DebugPlugin owns
  // window.__VIBEGAME__; the profiler/audio bridges merge into it.
  GAME.withPlugin(GAME.DebugPlugin);
  GAME.withPlugin(GAME.ProfilerPlugin);
  GAME.withSystem(TrackPropSpawnSystem);
  GAME.withSystem(BgmSystem);
  GAME.withSystem(BestLapSyncSystem);
  GAME.withSystem(CheckpointArrowSystem);
  GAME.withSystem(UiSetupSystem);
  GAME.configure({ canvas: '#game-canvas' });

  window.addEventListener('keydown', (e) => {
    if (e.code === 'KeyR') GAME.restartRace();
  });

  // Pick the mode *before* the engine parses the scene: `applyMode` removes
  // the rivals / toggles checkpoint-count, and the parsers only see the DOM
  // once.
  await preloadVehicles();
  await waitForMode();

  // Loading screen (same as simple-rpg): mounts here so the world parses and
  // streams behind an honest progress bar instead of a blank screen; the
  // LoadingScreenSystem fades it out once assets + terrain + shaders are in.
  const bootLang = detectLocale();
  GAME.setLoadingScreenLocale(bootLang);
  GAME.mountLoadingScreen({
    title: 'Sunset Ridge',
    subtitle:
      bootLang === 'pt' ? 'Preparando o circuito…' : 'Preparing the circuit…',
  });

  const runtime = await GAME.getBuilder().build();
  const state = runtime.getState();

  // QA surface — needs state and must run before runtime.start() parses the
  // scene. (Spawner road exclusion comes from the <Road flatten> ground brush
  // registered during setup, not from per-node footprints.)
  registerProfilerDebug(state);
  GAME.registerDebugVar(state, 'race', () => ({
    phase: GAME.getRaceState().phase,
    bestLap: persistedBest,
    standings: GAME.getStandings().map((eid) => ({
      eid,
      name: GAME.getVehicleName(eid),
      lap: GAME.RaceTracker.lap[eid],
      position: GAME.RaceTracker.position[eid],
      best: GAME.RaceTracker.bestLapTime[eid],
    })),
  }));
  GAME.registerDebugVar(state, 'camera', () => {
    const cam = GAME.defineQuery([GAME.ChaseCamera])(state.world)[0];
    return cam === undefined ? 'none' : GAME.getCameraModeName(cam);
  });
  GAME.registerDebugAction(state, 'restart', () => GAME.restartRace());

  await runtime.start();

  // Console handle for debugging: `__race.state()`, `__race.cars()`, …
  (window as unknown as { __race?: unknown }).__race = {
    state: GAME.getRaceState,
    standings: GAME.getStandings,
    restart: GAME.restartRace,
    cars: () =>
      GAME.getStandings().map((eid) => ({
        eid,
        name: GAME.getVehicleName(eid),
        s: GAME.Vehicle.trackS[eid],
        lateral: GAME.Vehicle.trackLateral[eid],
        speed: GAME.Vehicle.speed[eid],
        lap: GAME.RaceTracker.lap[eid],
        position: GAME.RaceTracker.position[eid],
        distance: GAME.RaceTracker.distance[eid],
        best: GAME.RaceTracker.bestLapTime[eid],
        finished: GAME.RaceTracker.finished[eid] === 1,
        pos: [
          GAME.Transform.posX[eid],
          GAME.Transform.posY[eid],
          GAME.Transform.posZ[eid],
        ],
      })),
    meshes: () => {
      const eid = GAME.getPrimaryTrackEntity();
      return eid === undefined ? null : GAME.getTrackMeshes(eid);
    },
    track: () => {
      const eid = GAME.getPrimaryTrackEntity();
      return eid === undefined ? null : GAME.getTrackSpline(eid);
    },
  };

  // The loading overlay fades out when the world is ready — release the grid
  // only then, so the countdown never runs behind the screen.
  await waitForLoadingDone();
  GAME.markRaceReady();
}

void bootstrap();

// Soft HMR of this graph leaks WebGL/KTX2/Rapier in Firefox — decline so Vite
// always full-reloads (same guard as simple-rpg).
setupHmrGuard(() => {
  resetTrackDressing();
  GAME.releaseRuntimeGpuResources();
});
