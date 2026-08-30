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
import * as GAME from 'aigamekit-vibegame';
import { registerGameSounds, preloadGameSounds } from './game/sounds';
import {
  BED_MARGIN,
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

/** First element with this tag name anywhere in the assembled world. */
function findTag(
  root: GAME.ParsedElement,
  tagName: string
): GAME.ParsedElement | undefined {
  return GAME.findElements(root, (el) => el.tagName === tagName)[0];
}

/**
 * Fill in the generated geometry on the tags authored in `public/world/*.xml`.
 *
 * The circuit lives in its own include, so it does not exist in `document`
 * when this runs — the engine only assembles the world during `GAME.run()`.
 * `GAME.onWorldXml` hands us the expanded tree at exactly the right moment:
 * after the includes, before there are entities.
 */
function injectTrack(root: GAME.ParsedElement): void {
  const el = findTag(root, 'RaceTrack');
  if (!el) return;
  el.attributes.centerline = centerlineAttribute();
  el.attributes.widths = widthsAttribute();
  el.attributes.sections = sectionsAttribute();

  // Fill the road bed `<Road>` from the same spline so the carve follows the
  // circuit exactly. The spline needs the centerline attributes set above, so
  // this runs after.
  //
  // Four parallel lists, not just the path: the carver grades the bed to the
  // *authored* elevation (`heights`), matches the per-corner width (`widths`)
  // and tilts the ground with the banking (`banks`). Surveying the terrain
  // instead — the default for a village lane — would put the bed wherever the
  // hills happen to be and leave the circuit hanging over it.
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
  const heights: number[] = [];
  const widths: number[] = [];
  const banks: number[] = [];
  const round = (v: number, d = 2) => Number(v.toFixed(d));
  const pushStation = (s: number) => {
    const f = spline.sampleAt(s);
    path.push(round(f.x, 1), round(f.z, 1));
    // Bed = driving surface minus the suspension height, so the track ribbon
    // sits exactly TRACK_ELEVATION above ground the whole way round.
    heights.push(round(f.y - TRACK_ELEVATION));
    // Bed covers the racing surface plus the kerbs and the wall footing.
    widths.push(round(f.width + BED_MARGIN, 1));
    banks.push(round((f.bank * 180) / Math.PI));
  };
  for (let s = 0; s < spline.length; s += 6) pushStation(s);
  // Close the loop on the exact start station (flatten-closed needs the
  // duplicated node) so there is no step across the start/finish line.
  pushStation(0);

  const road = findTag(root, 'Road');
  if (!road) return;
  road.attributes.path = path.join(' ');
  road.attributes.heights = heights.join(' ');
  road.attributes.widths = widths.join(' ');
  road.attributes.banks = banks.join(' ');
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

export type RaceMode = 'race' | 'time-trial' | 'weekend';

/** Chosen at the menu, read by the world hook when the scene is assembled. */
let raceMode: RaceMode = 'race';

/** Record the chosen mode; the world hook applies it at parse time. */
export function applyMode(mode: RaceMode): void {
  raceMode = mode;
}

/**
 * Apply the chosen mode to the assembled world.
 *
 * - Race: keep the `<AiVehicle>` rivals; disable checkpoint respawns (a
 *   full grid races to the flag, off-track is the driver's problem).
 * - Time Trial: drop the rivals; enable checkpoint respawn so a crash costs
 *   time, not the race — and freeze the hazards seed so every lap (and every
 *   ghost comparison) runs the same item rows and obstacle set.
 *
 * The rivals live in `world/grid.xml`, so this prunes the parsed tree rather
 * than the DOM — the include is never in `document` to begin with.
 */
function applyModeToWorld(root: GAME.ParsedElement): void {
  const track = findTag(root, 'RaceTrack');
  if (raceMode === 'time-trial') {
    const prune = (node: GAME.ParsedElement): void => {
      node.children = node.children.filter((c) => c.tagName !== 'AiVehicle');
      for (const child of node.children) prune(child);
    };
    prune(root);
    if (track) track.attributes['checkpoint-count'] = '8';
    const layout = findTag(root, 'HazardsLayout');
    if (layout) layout.attributes.seed = '1971';
  } else if (track) {
    delete track.attributes['checkpoint-count'];
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
function waitForMode(): Promise<{
  mode: RaceMode;
  condition: GAME.TrackCondition;
}> {
  const overlay = document.getElementById('loading');
  const canvas = document.getElementById(
    'game-canvas'
  ) as HTMLCanvasElement | null;
  const prompt = overlay?.querySelector('.sub');
  const menu = document.getElementById('mode-menu');
  const condMenu = document.getElementById('cond-menu');
  const buttons = menu ? [...menu.querySelectorAll('button')] : [];
  if (prompt) prompt.textContent = 'Choose a mode';
  overlay?.classList.add('ready');
  menu?.classList.remove('hidden');
  // DOM drifted from index.html (renamed ids, missing menu): without this the
  // promise never resolves and the game hangs on "Choose a mode" forever.
  if (!overlay || buttons.length === 0) {
    console.warn('[racer] mode menu missing — starting a default race');
    return Promise.resolve({ mode: 'race', condition: 'dry' });
  }

  return new Promise((resolve) => {
    let condition: GAME.TrackCondition = 'dry';
    const onCond = (e: Event): void => {
      e.stopPropagation();
      const next = (e.currentTarget as HTMLElement).dataset.condition as
        GAME.TrackCondition | undefined;
      if (!next) return;
      condition = next;
      for (const b of condMenu?.querySelectorAll('button') ?? []) {
        b.classList.toggle('selected', b === e.currentTarget);
      }
    };
    const cleanup = (): void => {
      for (const btn of buttons) btn.removeEventListener('click', onPick);
      for (const btn of condMenu?.querySelectorAll('button') ?? []) {
        btn.removeEventListener('click', onCond);
      }
    };
    const pick = (mode: RaceMode): void => {
      cleanup();
      applyMode(mode);
      overlay.classList.add('hidden');
      canvas?.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
      const stealFocus = (): void => {
        canvas?.focus();
        if (document.activeElement !== canvas) {
          requestAnimationFrame(stealFocus);
        }
      };
      requestAnimationFrame(stealFocus);
      resolve({ mode, condition });
    };
    const onPick = (e: Event): void => {
      const mode = (e.currentTarget as HTMLElement).dataset.mode as
        RaceMode | undefined;
      if (mode) pick(mode);
    };
    for (const btn of buttons) btn.addEventListener('click', onPick);
    for (const btn of condMenu?.querySelectorAll('button') ?? []) {
      btn.addEventListener('click', onCond);
    }
  });
}

/** Music: the race loop while driving, the menu loop over the results screen. */
const BgmSystem = GAME.createMusicLayerDriver({
  // Pódio 'finished' → tema de créditos do pool; grid/countdown/racing
  // mantêm a faixa de corrida (comportamento anterior).
  resolve: () =>
    GAME.getRaceState().phase === 'finished' ? 'credits' : 'race',
});

// ── Best lap persistence: same player-scoped save serializer pattern as
//    simple-rpg. The race director zeroes bestLapTime when it places cars on
//    the grid, so a small system re-applies the persisted best (and captures
//    new records) — the HUD "Best" line reads bestLapTime[player]. ───────────
const SAVE_KEY = 'simple-racer-save';
const PB_KEY = 'simple-racer-pb';
const BEST_LAP_KIND = 'racer-best-lap';
let persistedBest = 0;
let lastGhostDuration = 0;

function persistPersonalBest(): void {
  try {
    localStorage.setItem(
      PB_KEY,
      JSON.stringify({
        best: persistedBest > 0 ? persistedBest : undefined,
        ghost: GAME.serializeGhostLap(GAME.getGhostLap()),
      })
    );
  } catch {
    // Quota / private mode — the in-memory PB still works this session.
  }
}

function hydratePersonalBest(): void {
  try {
    const raw = localStorage.getItem(PB_KEY);
    if (!raw) return;
    const d = JSON.parse(raw) as { best?: number; ghost?: unknown };
    if (typeof d.best === 'number' && d.best > 0) persistedBest = d.best;
    const ghost = GAME.parseGhostLap(d.ghost);
    if (ghost) {
      GAME.setGhostLap(ghost);
      lastGhostDuration = ghost.duration;
    }
  } catch {
    // Corrupt payload — start a fresh PB rather than crash boot.
  }
}

function registerBestLapSerializer(state: GAME.State): void {
  GAME.registerSaveSerializer(state, BEST_LAP_KIND, {
    serialize: (s, eid) => {
      if (s.getEntityByName('player') !== eid) return null;
      if (persistedBest <= 0 && !GAME.getGhostLap()) return null;
      return {
        best: persistedBest > 0 ? persistedBest : undefined,
        ghost: GAME.serializeGhostLap(GAME.getGhostLap()),
      };
    },
    deserialize: (s, eid, data) => {
      if (s.getEntityByName('player') !== eid) return;
      // Untrusted payload (old save, hand edit): a null here would throw inside
      // the load pass and abort every other serializer after this one.
      const d = (typeof data === 'object' && data !== null ? data : {}) as {
        best?: unknown;
        ghost?: unknown;
      };
      if (typeof d.best === 'number' && Number.isFinite(d.best) && d.best > 0)
        persistedBest = d.best;
      const ghost = GAME.parseGhostLap(d.ghost);
      if (ghost) {
        GAME.setGhostLap(ghost);
        lastGhostDuration = ghost.duration;
      }
      persistPersonalBest();
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
    if (current <= 0) {
      if (persistedBest > 0) GAME.RaceTracker.bestLapTime[eid] = persistedBest;
      return;
    }
    let dirty = false;
    if (persistedBest <= 0 || current < persistedBest - 0.01) {
      persistedBest = current;
      dirty = true;
      GAME.spawnFloatingText(state, 'NEW BEST LAP!', {
        x: GAME.Transform.posX[eid],
        y: GAME.Transform.posY[eid] + 2.8,
        z: GAME.Transform.posZ[eid],
        duration: 2.4,
        color: '#7fe0a0',
      });
    }
    const ghostDur = GAME.getGhostLap()?.duration ?? 0;
    if (
      ghostDur > 0 &&
      (lastGhostDuration <= 0 || ghostDur < lastGhostDuration - 0.01)
    ) {
      lastGhostDuration = ghostDur;
      dirty = true;
    }
    if (dirty) persistPersonalBest();
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
    // trackS can be negative behind the start line (grid / rollback): a raw
    // floor%N would produce `cp--1`, an id that matches no waypoint.
    const sector =
      ((Math.floor((GAME.Vehicle.trackS[eid] / trackLength) * SECTOR_COUNT) %
        SECTOR_COUNT) +
        SECTOR_COUNT) %
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
      console.warn(
        '[racer] loading screen never finished — releasing the grid'
      );
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
          'Item: J or 1   Horn: H   Look back: V   Camera: C   Restart: R   Pause: Q\n' +
          'Drift: hold Space through the corner, release for a mini-turbo\n' +
          'Launch: hold W on the grid — green zone = rocket start\n' +
          'Slipstream: tuck behind a rival · Ghost: beat your PB lap',
      },
      pt: {
        'modal.tab.options': 'Opções',
        'options.restart': '🔁 Recomeçar',
        'options.controls':
          'Dirigir: WASD / Setas   Freio de mão: Espaço   Nitro: Shift\n' +
          'Item: J ou 1   Buzina: H   Olhar atrás: V   Câmera: C   Recomeçar: R   Pausa: Q\n' +
          'Drift: segura Espaço na curva, larga para o mini-turbo\n' +
          'Arranque: segura W na grelha — zona verde = arranque foguete\n' +
          'Slipstream: cola no rival · Ghost: bate a tua volta PB',
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
  hydratePersonalBest();

  // Everything the world XML cannot say for itself, in one place: the circuit
  // geometry (generated from src/track.ts) and the race mode chosen at the
  // menu. The hook fires inside GAME.run(), once the includes are assembled.
  GAME.onWorldXml((root) => {
    injectTrack(root);
    applyModeToWorld(root);
  });

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
  // No `collider="shape: precompute"` anywhere in this world — drop the plugin
  // so the engine never fetches gameassets_handoff.json (absent here; the
  // shared Vale GLBs came from the RPG, not a racer-side handoff).
  GAME.withoutPlugins(GAME.PrecomputePlugin);
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
    // Buzina (pool partilhado) — só com a corrida em curso e sem modais.
    // KeyH faz parte do cluster de comandos da home row (H buzina, J item),
    // dedicado a ações para não tirar a mão do WASD.
    if (
      e.code === 'KeyH' &&
      GAME.getRaceState().phase === 'racing' &&
      !e.repeat
    ) {
      GAME.playSound('race-horn');
    }
  });

  // Pick the mode *before* the engine parses the scene: `applyMode` removes
  // the rivals / toggles checkpoint-count, and the parsers only see the DOM
  // once.
  await preloadVehicles();
  const choice = await waitForMode();

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
  GAME.setRaceState({
    session: choice.mode === 'weekend' ? 'qualifying' : 'race',
    condition: choice.condition,
  });

  // QA surface — needs state and must run before runtime.start() parses the
  // scene. (Spawner road exclusion comes from the <Road flatten> ground brush
  // registered during setup, not from per-node footprints.)
  registerProfilerDebug(state);
  GAME.registerDebugVar(state, 'race', () => ({
    phase: GAME.getRaceState().phase,
    session: GAME.getRaceState().session,
    condition: GAME.getRaceState().condition,
    bestLap: persistedBest,
    ghost: GAME.getGhostLap()?.duration ?? null,
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
    ghost: GAME.getGhostLap,
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
        driftDir: GAME.Vehicle.driftDir[eid],
        driftCharge: GAME.Vehicle.driftCharge[eid],
        miniTurbo: GAME.Vehicle.miniTurbo[eid],
        airborne: GAME.Vehicle.airborne[eid] === 1,
        spin: GAME.Vehicle.spinOutTimer[eid],
        launchRev: GAME.Vehicle.launchRev[eid],
        wheelspin: GAME.Vehicle.wheelspin[eid],
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

void bootstrap().catch((err) => {
  // Boot failures (fetch, parse, WebGL) must not reject unhandled behind an
  // eternal "Choose a mode" / loading overlay.
  console.error('[racer] boot failed:', err);
  const overlay = document.getElementById('loading');
  const sub = overlay?.querySelector('.sub');
  if (sub)
    (sub as HTMLElement).textContent =
      'Boot failed — check the console (F12) and reload. ' +
      `${(err as Error)?.message ?? err}`;
});

// Soft HMR of this graph leaks WebGL/KTX2/Rapier in Firefox — decline so Vite
// always full-reloads (same guard as simple-rpg).
setupHmrGuard(() => {
  resetTrackDressing();
  GAME.releaseRuntimeGpuResources();
});
