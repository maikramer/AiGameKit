import { defineSystem, defineQuery, type State, type System } from '../../core';
import { getSoundDef, playSound } from '../audio';
import { Vehicle, PlayerVehicle, RaceTracker, Track } from './components';
import { getTrackSpline } from './data';
import { createFrame, type TrackSpline } from './spline';
import { placeVehicleOnTrack } from './vehicle-control';
import {
  getRaceState,
  isRaceReady,
  setRaceState,
  resetRaceState,
  type RaceResult,
} from './race-state';

const trackerQuery = defineQuery([RaceTracker, Vehicle]);
const trackQuery = defineQuery([Track]);
const playerQuery = defineQuery([PlayerVehicle, Vehicle]);

/** Seconds of red lights before the start. */
const COUNTDOWN_FROM = 3;
/** Extra seconds on the grid after everything is ready (engines settle). */
const GRID_SETTLE = 0.9;
/** Arc position of pole (m past the start line) and spacing between slots. */
// 35 m, not 6: on a circuit whose closing node pulls the Catmull-Rom sideways
// at s = 0, a car launched at s = 6 gets a ~9° heading error and drifts into
// the inside wall before the first corner. By s = 35 most straights have
// settled to their true tangent.
const GRID_FIRST_S = 35;
const GRID_ROW_SPACING = 7;
/** Lateral offset of the two grid columns (m from the centerline). */
const GRID_COLUMN_OFFSET = 2.6;
/** Seconds pointing backwards before the wrong-way warning shows. */
const WRONG_WAY_DELAY = 0.7;

const _frame = createFrame();

let gridSettleTimer = 0;
let lastBeepSecond = -1;
let lastGeneration = -1;
/** Cars in classification order, rebuilt every frame while racing. */
const standings: number[] = [];
/** Human-readable name per car, for the results table. */
const carNames = new Map<number, string>();

/** Give a car a display name (used by the HUD and the results screen). */
export function setVehicleName(eid: number, name: string): void {
  carNames.set(eid, name);
}

export function getVehicleName(eid: number): string {
  return carNames.get(eid) ?? `Car ${eid}`;
}

/** Live classification, leader first. */
export function getStandings(): readonly number[] {
  return standings;
}

/** Fire a race SFX only when the game actually registered that bank key. */
function playRaceSfx(key: string): void {
  if (getSoundDef(key)) playSound(key);
}

/** Park every entrant on its grid slot, engines off, facing down the road. */
function formUpGrid(state: State, spline: TrackSpline): void {
  const cars = trackerQuery(state.world);
  const player = playerQuery(state.world)[0];
  // Pole goes to the player when there is one, then the rest in entity order:
  // an arcade racer that starts the human at the back of a 6-car grid on lap 1
  // of 3 is not a fair fight.
  const ordered = [...cars].sort((a, b) => {
    if (a === player) return -1;
    if (b === player) return 1;
    return a - b;
  });

  ordered.forEach((eid, slot) => {
    RaceTracker.gridSlot[eid] = slot;
    const row = Math.floor(slot / 2);
    const column = slot % 2 === 0 ? -1 : 1;
    const s = GRID_FIRST_S + row * GRID_ROW_SPACING;
    placeVehicleOnTrack(eid, spline, s, column * GRID_COLUMN_OFFSET);

    RaceTracker.lap[eid] = 0;
    RaceTracker.lastS[eid] = Vehicle.trackS[eid];
    RaceTracker.distance[eid] = 0;
    RaceTracker.lapStartTime[eid] = 0;
    RaceTracker.bestLapTime[eid] = -1;
    RaceTracker.lastLapTime[eid] = -1;
    RaceTracker.finished[eid] = 0;
    RaceTracker.finishTime[eid] = -1;
    RaceTracker.position[eid] = slot + 1;
    RaceTracker.wrongWay[eid] = 0;
    RaceTracker.wrongWayTimer[eid] = 0;
    RaceTracker.lastCheckpointIndex[eid] = 0;
    RaceTracker.lastCheckpointS[eid] = 0;
    RaceTracker.offTrackTimer[eid] = 0;
    RaceTracker.stuckTimer[eid] = 0;
    RaceTracker.stuckS[eid] = Vehicle.trackS[eid];
    RaceTracker.respawnFlash[eid] = 0;
  });
}

/**
 * Owns the race: grid formation, the countdown, lap and position scoring,
 * wrong-way detection, the chequered flag and the classification.
 *
 * Lap counting is distance-based. Each car accumulates the signed shortest arc
 * delta every frame, so `laps = floor((distance + gridS) / trackLength)` is
 * exact — it cannot be fooled by reversing over the line, by a projection
 * glitch where the circuit crosses itself, or by a frame long enough to skip
 * the line entirely. The old "arc fraction jumped from 0.9 to 0.1" heuristic
 * failed all three.
 */
export const RaceDirectorSystem: System = defineSystem({
  name: 'RaceDirectorSystem',
  group: 'simulation',

  update(state: State) {
    const race = getRaceState();
    const trackEid = trackQuery(state.world)[0];
    if (trackEid === undefined) return;
    const spline = getTrackSpline(trackEid);
    if (!spline) return;

    const cars = trackerQuery(state.world);
    if (cars.length === 0) return;
    const player = playerQuery(state.world)[0];
    const dt = state.time.deltaTime;

    // ---- Restart requested (generation bumped by restartRace) --------------
    if (race.generation !== lastGeneration && race.phase === 'grid') {
      lastGeneration = race.generation;
      gridSettleTimer = 0;
      lastBeepSecond = -1;
      formUpGrid(state, spline);
      setRaceState({
        track: trackEid,
        playerVehicle: player ?? 0,
        totalLaps: Track.totalLaps[trackEid] || 3,
        entrants: cars.length,
        raceTime: 0,
        countdown: COUNTDOWN_FROM,
      });
      return;
    }

    switch (race.phase) {
      // ---- First frame with a track and cars: form up ---------------------
      case 'idle': {
        Track.length[trackEid] = spline.length;
        lastGeneration = race.generation;
        gridSettleTimer = 0;
        lastBeepSecond = -1;
        formUpGrid(state, spline);
        setRaceState({
          phase: 'grid',
          track: trackEid,
          playerVehicle: player ?? 0,
          totalLaps: Track.totalLaps[trackEid] || 3,
          entrants: cars.length,
          countdown: COUNTDOWN_FROM,
          raceTime: 0,
          results: [],
        });
        break;
      }

      // ---- Waiting for the game's assets, then the lights -----------------
      case 'grid': {
        if (!isRaceReady()) {
          gridSettleTimer = 0;
          break;
        }
        gridSettleTimer += dt;
        if (gridSettleTimer >= GRID_SETTLE) {
          setRaceState({ phase: 'countdown', countdown: COUNTDOWN_FROM });
          lastBeepSecond = -1;
        }
        break;
      }

      case 'countdown': {
        const remaining = race.countdown - dt;
        if (remaining <= 0) {
          setRaceState({ phase: 'racing', countdown: 0, raceTime: 0 });
          for (const eid of cars) {
            RaceTracker.lapStartTime[eid] = 0;
            RaceTracker.lastS[eid] = Vehicle.trackS[eid];
          }
          playRaceSfx('race-go');
        } else {
          setRaceState({ countdown: remaining });
          const beep = Math.ceil(remaining);
          if (beep >= 1 && beep !== lastBeepSecond) {
            lastBeepSecond = beep;
            playRaceSfx('race-countdown');
          }
        }
        break;
      }

      case 'racing':
      case 'finished': {
        const raceTime = race.raceTime + dt;
        setRaceState({ raceTime });
        scoreLaps(state, spline, cars, raceTime, race.totalLaps, player);
        rankCars(cars);
        if (
          race.phase === 'racing' &&
          player !== undefined &&
          RaceTracker.finished[player]
        ) {
          setRaceState({
            phase: 'finished',
            results: buildResults(cars, player),
          });
          playRaceSfx('race-finish');
        }
        break;
      }
    }
  },

  dispose() {
    resetRaceState();
    standings.length = 0;
    carNames.clear();
    lastGeneration = -1;
    gridSettleTimer = 0;
    lastBeepSecond = -1;
  },
});

/** Advance each car's distance, laps, lap times, wrong-way flag and finish state. */
function scoreLaps(
  state: State,
  spline: TrackSpline,
  cars: readonly number[],
  raceTime: number,
  totalLaps: number,
  player: number | undefined
): void {
  const length = spline.length;
  for (const eid of cars) {
    const s = Vehicle.trackS[eid];
    const delta = spline.deltaS(s, RaceTracker.lastS[eid]);
    RaceTracker.lastS[eid] = s;

    if (RaceTracker.finished[eid]) continue;

    RaceTracker.distance[eid] += delta;

    // ---- Wrong way ------------------------------------------------------
    const f = spline.sampleAt(s, _frame);
    const trackHeading = Math.atan2(f.tx, f.tz);
    let headingErr = Vehicle.heading[eid] - trackHeading;
    while (headingErr > Math.PI) headingErr -= Math.PI * 2;
    while (headingErr < -Math.PI) headingErr += Math.PI * 2;
    const facingBackwards =
      Math.cos(headingErr) * Math.sign(Vehicle.speed[eid] || 1) < -0.15;
    const moving = Math.abs(Vehicle.speed[eid]) > 3;
    if (facingBackwards && moving) {
      RaceTracker.wrongWayTimer[eid] += state.time.deltaTime;
    } else {
      RaceTracker.wrongWayTimer[eid] = 0;
    }
    RaceTracker.wrongWay[eid] =
      RaceTracker.wrongWayTimer[eid] > WRONG_WAY_DELAY ? 1 : 0;

    // ---- Laps -------------------------------------------------------------
    // Grid slots sit a few metres *past* the start line, so the first crossing
    // is a genuine full lap later.
    const gridS =
      GRID_FIRST_S +
      Math.floor(RaceTracker.gridSlot[eid] / 2) * GRID_ROW_SPACING;
    const lapsNow = Math.max(
      0,
      Math.floor((RaceTracker.distance[eid] + gridS) / length)
    );
    if (lapsNow > RaceTracker.lap[eid]) {
      const lapTime = raceTime - RaceTracker.lapStartTime[eid];
      RaceTracker.lastLapTime[eid] = lapTime;
      if (
        RaceTracker.bestLapTime[eid] <= 0 ||
        lapTime < RaceTracker.bestLapTime[eid]
      ) {
        RaceTracker.bestLapTime[eid] = lapTime;
      }
      RaceTracker.lapStartTime[eid] = raceTime;
      RaceTracker.lap[eid] = lapsNow;
      if (eid === player) playRaceSfx('race-lap');

      if (lapsNow >= totalLaps) {
        RaceTracker.finished[eid] = 1;
        RaceTracker.finishTime[eid] = raceTime;
      }
    }
  }
}

/** Sort cars into live positions: finishers by finish time, then by distance. */
function rankCars(cars: readonly number[]): void {
  standings.length = 0;
  for (const eid of cars) standings.push(eid);
  standings.sort((a, b) => {
    const fa = RaceTracker.finished[a];
    const fb = RaceTracker.finished[b];
    if (fa !== fb) return fb - fa;
    if (fa && fb) return RaceTracker.finishTime[a] - RaceTracker.finishTime[b];
    const byDistance = RaceTracker.distance[b] - RaceTracker.distance[a];
    // On the grid every car has covered zero metres; fall back to the grid
    // order so the standings read 1..N instead of an arbitrary entity order.
    if (Math.abs(byDistance) > 1e-4) return byDistance;
    return RaceTracker.gridSlot[a] - RaceTracker.gridSlot[b];
  });
  standings.forEach((eid, i) => {
    RaceTracker.position[eid] = i + 1;
  });
}

function buildResults(
  cars: readonly number[],
  player: number | undefined
): RaceResult[] {
  rankCars(cars);
  return standings.map((eid, i) => ({
    entity: eid,
    name: getVehicleName(eid),
    position: i + 1,
    totalTime: RaceTracker.finished[eid] ? RaceTracker.finishTime[eid] : -1,
    bestLap: RaceTracker.bestLapTime[eid],
    laps: RaceTracker.lap[eid],
    isPlayer: eid === player,
  }));
}
