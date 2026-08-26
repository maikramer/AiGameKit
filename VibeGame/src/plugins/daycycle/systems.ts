import { defineSystem, defineQuery, type State, type System } from '../../core';
import { GameClock } from './components';
import { daylightFactor, quantizeAngle, sunAngles } from './sun';
import { ProceduralSky } from '../sky';
import { AmbientLight } from '../rendering';
import { SEASON_COUNT } from './calendar';

const clockQuery = defineQuery([GameClock]);
const skyQuery = defineQuery([ProceduralSky]);
const ambientQuery = defineQuery([AmbientLight]);

/**
 * Advance the wall clock and roll the calendar. Runs in `simulation`; games
 * hang day-driven rules (crop growth, shop stock) off the DAY_ADVANCED
 * callback instead of polling.
 */
export const DayCycleSystem: System = defineSystem({
  name: 'DayCycleSystem',
  group: 'simulation',
  update: (state) => {
    const dt = state.time.deltaTime;
    for (const eid of clockQuery(state.world)) {
      if (GameClock.paused[eid] === 1) continue;
      const minutes = dt * (GameClock.minutesPerRealSecond[eid] || 1);
      advanceClockMinutes(state, eid, minutes);
    }
  },
});

/**
 * Push the (quantized) sun into the sky and the ambient ramp. Runs BEFORE
 * ProceduralSkySystem so the sky reads the new angles in the same frame;
 * the quantization is what keeps its PMREM rebuild to ~1 per band crossing
 * instead of one per frame.
 */
export const DayCycleSkySystem: System = defineSystem({
  name: 'DayCycleSkySystem',
  group: 'draw',
  before: ['ProceduralSkySystem'],
  update: (state) => {
    for (const eid of clockQuery(state.world)) {
      const arc = {
        dawnMinute: GameClock.dawnMinute[eid],
        duskMinute: GameClock.duskMinute[eid],
        sunAzimuthBase: GameClock.sunAzimuthBase[eid],
        maxSunElevation: GameClock.maxSunElevation[eid],
        minSunElevation: GameClock.minSunElevation[eid],
      };
      const sun = sunAngles(GameClock.minuteOfDay[eid], arc);
      const factor = daylightFactor(sun.elevation);
      const step = GameClock.skyStepDeg[eid];

      if (GameClock.driveSky[eid] === 1) {
        for (const sky of skyQuery(state.world)) {
          ProceduralSky.sunElevation[sky] = quantizeAngle(sun.elevation, step);
          ProceduralSky.sunAzimuth[sky] = quantizeAngle(sun.azimuth, step);
          break; // one sky, one sun
        }
      }
      if (GameClock.driveAmbient[eid] === 1) {
        const night = GameClock.ambientNightIntensity[eid];
        const day = GameClock.ambientDayIntensity[eid];
        for (const light of ambientQuery(state.world)) {
          AmbientLight.intensity[light] = night + (day - night) * factor;
          break;
        }
      }
    }
  },
});

/** Advance by whole minutes through the rollover pipeline. Returns events fired. */
export function advanceClockMinutes(
  state: State,
  eid: number,
  minutes: number
): string[] {
  const events: string[] = [];
  GameClock.minuteOfDay[eid] += minutes;
  while (GameClock.minuteOfDay[eid] >= 1440) {
    GameClock.minuteOfDay[eid] -= 1440;
    rollDay(state, eid, events);
  }
  return events;
}

function rollDay(state: State, eid: number, events: string[]): void {
  GameClock.day[eid]++;
  events.push(DAY_ADVANCED);
  notify(state, DAY_ADVANCED, eid);
  if (GameClock.day[eid] > GameClock.daysPerSeason[eid]) {
    GameClock.day[eid] = 1;
    GameClock.season[eid] = (GameClock.season[eid] + 1) % SEASON_COUNT;
    events.push(SEASON_CHANGED);
    notify(state, SEASON_CHANGED, eid);
    if (GameClock.season[eid] === 0) {
      GameClock.year[eid]++;
      events.push(YEAR_CHANGED);
      notify(state, YEAR_CHANGED, eid);
    }
  }
}

// --- Events: a local callback registry — this plugin must work in a game
//     with nothing but DefaultPlugins, so no rpg-core EventBus dependency. ---

export const DAY_ADVANCED = 'daycycle:day-advanced';
export const SEASON_CHANGED = 'daycycle:season-changed';
export const YEAR_CHANGED = 'daycycle:year-changed';

export type ClockListener = (eid: number) => void;

const listeners = new WeakMap<State, Map<string, Set<ClockListener>>>();

function notify(state: State, event: string, eid: number): void {
  const set = listeners.get(state)?.get(event);
  if (!set) return;
  for (const listener of set) listener(eid);
}

/** Subscribe to a daycycle event; returns an unsubscribe function. */
export function onClockEvent(
  state: State,
  event: string,
  listener: ClockListener
): () => void {
  let map = listeners.get(state);
  if (!map) {
    map = new Map();
    listeners.set(state, map);
  }
  let set = map.get(event);
  if (!set) {
    set = new Set();
    map.set(event, set);
  }
  set.add(listener);
  return () => set!.delete(listener);
}
