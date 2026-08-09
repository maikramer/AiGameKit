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
  centerlineAttribute,
  sectionsAttribute,
  widthsAttribute,
} from './track';
import { TrackPropSpawnSystem } from './game/track-props';

/** Fill in the `<RaceTrack>` geometry attributes before the engine parses it. */
function injectTrack(): void {
  const el = document.querySelector('RaceTrack');
  if (!el) return;
  el.setAttribute('centerline', centerlineAttribute());
  el.setAttribute('widths', widthsAttribute());
  el.setAttribute('sections', sectionsAttribute());
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
let bgmLayer: 'race' | 'menu' | null = null;
let bgmHandle: { stop(): void } | null = null;

const BgmSystem: GAME.System = {
  name: 'BgmSystem',
  group: 'simulation',
  update() {
    // Howler only starts once the browser has had a user gesture.
    const ctx = (window as unknown as { Howler?: { ctx?: AudioContext } })
      .Howler?.ctx;
    if (ctx?.state !== 'running') return;

    const phase = GAME.getRaceState().phase;
    const layer: 'race' | 'menu' = phase === 'finished' ? 'menu' : 'race';
    if (bgmLayer === layer) return;
    bgmLayer = layer;

    bgmHandle?.stop();
    bgmHandle = GAME.playSound(`bgm-${layer}`, { origin: 'music' });
  },
};

async function bootstrap(): Promise<void> {
  registerGameSounds();
  preloadGameSounds();
  injectTrack();

  GAME.holdRaceOnGrid();
  GAME.withSystem(TrackPropSpawnSystem);
  GAME.withSystem(BgmSystem);

  window.addEventListener('keydown', (e) => {
    if (e.code === 'KeyR') GAME.restartRace();
  });

  // Pick the mode *before* the engine parses the scene: `applyMode` removes
  // the rivals / toggles checkpoint-count, and the parsers only see the DOM
  // once.
  await preloadVehicles();
  await waitForMode();
  await GAME.run();

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

  GAME.markRaceReady();
}

void bootstrap();
