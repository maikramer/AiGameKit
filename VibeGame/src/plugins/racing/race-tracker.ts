import { defineSystem, defineQuery, type State, type System } from '../../core';
import { WorldTransform } from '../transforms';
import { getSoundDef, playSound } from '../audio';
import {
  RaceTracker,
  Track,
  PlayerVehicle,
  Vehicle,
} from './components';
import {
  computeTrackMetrics,
  getTrackData,
  sampleProgress,
  type TrackMetrics,
} from './data';
import {
  getRaceState,
  resetRaceState,
  setRaceState,
  type RacePhase,
} from './race-state';

const trackerQuery = defineQuery([RaceTracker, Vehicle]);
const trackQuery = defineQuery([Track]);
const playerQuery = defineQuery([PlayerVehicle, Vehicle]);

// Cached metrics per track entity (recomputed only when the track changes).
const metricsCache = new Map<number, TrackMetrics>();

const COUNTDOWN_FROM = 3; // seconds

// Last countdown second we beeped for (ceil), so the 3-2-1 ticks fire once each.
let lastBeepedSecond = -1;

/** Fire a bank sound when the example defines it (no-op + no warnings otherwise). */
function playRaceSfx(key: string): void {
  if (getSoundDef(key)) playSound(key);
}

/**
 * Owns the race phase machine (idle -> countdown -> racing -> finished) and the
 * per-vehicle lap/checkpoint scoring. Runs in `simulation` (once per frame, after
 * input) so the HUD reads fresh state the same frame.
 *
 * Lap detection: project the vehicle onto the track centerline, track the arc
 * fraction, and a lap is scored when the fraction wraps from ~1.0 back to ~0.0
 * going forward. Cheating (reversing over the line) is ignored because we only
 * count a crossing when the previous fraction was high and the new one is low.
 */
export const RaceTrackerSystem: System = defineSystem({
  name: 'RaceTrackerSystem',
  group: 'simulation',

  update(state: State) {
    // --- One-time setup: bind the singleton state to entities ---------------
    const race = getRaceState();
    if (race.phase === 'idle') {
      const trackEntity = trackQuery(state.world)[0];
      const playerEntity = playerQuery(state.world)[0];
      if (trackEntity !== undefined && playerEntity !== undefined) {
        // Precompute metrics + length on the Track component.
        const data = getTrackData(state, trackEntity);
        if (data) {
          const metrics = computeTrackMetrics(data);
          metricsCache.set(trackEntity, metrics);
          Track.length[trackEntity] = metrics.total;
        }
        setRaceState({
          track: trackEntity,
          playerVehicle: playerEntity,
          totalLaps: Track.totalLaps[trackEntity] || 3,
          phase: 'countdown',
          countdown: COUNTDOWN_FROM,
          raceStartTime: 0,
          finishTime: 0,
        });
        lastBeepedSecond = -1;
      }
    }

    // --- Countdown -------------------------------------------------------
    if (race.phase === 'countdown') {
      const remaining = race.countdown - state.time.deltaTime;
      if (remaining <= 0) {
        setRaceState({
          phase: 'racing',
          countdown: 0,
          raceStartTime: state.time.realtimeSinceStartup,
        });
        // Stamp lap start times so lap 1 timing is correct.
        for (const eid of trackerQuery(state.world)) {
          RaceTracker.lapStartTime[eid] = state.time.realtimeSinceStartup;
        }
        playRaceSfx('race-go');
      } else {
        setRaceState({ countdown: remaining });
        // Beep once per integer second shown (3 → 2 → 1).
        const n = Math.ceil(remaining);
        if (n >= 1 && n !== lastBeepedSecond) {
          lastBeepedSecond = n;
          playRaceSfx('race-countdown');
        }
      }
    }

    // --- Lap scoring -----------------------------------------------------
    if (race.phase === 'racing' || race.phase === 'finished') {
      const trackEntity = race.track;
      const data = trackEntity ? getTrackData(state, trackEntity) : undefined;
      const metrics = trackEntity ? metricsCache.get(trackEntity) : undefined;
      const totalLaps = race.totalLaps;
      const now = state.time.realtimeSinceStartup;

      for (const eid of trackerQuery(state.world)) {
        if (!data || !metrics) continue;
        if (RaceTracker.finished[eid]) continue;

        const x = WorldTransform.posX[eid];
        const z = WorldTransform.posZ[eid];
        const sample = sampleProgress(
          data,
          metrics,
          x,
          z,
          RaceTracker.nextCheckpoint[eid]
        );

        const prevFraction = RaceTracker.lastProgress[eid];
        RaceTracker.lastProgress[eid] = sample.fraction;
        RaceTracker.nextCheckpoint[eid] = sample.segment;

        // Lap wrap detection (closed circuit only).
        if (data.closed && prevFraction > 0.65 && sample.fraction < 0.2) {
          // Forward crossing of the start/finish line.
          const lapTime = now - RaceTracker.lapStartTime[eid];
          RaceTracker.lastLapTime[eid] = lapTime;
          if (
            RaceTracker.bestLapTime[eid] <= 0 ||
            lapTime < RaceTracker.bestLapTime[eid]
          ) {
            RaceTracker.bestLapTime[eid] = lapTime;
          }
          RaceTracker.lapStartTime[eid] = now;
          RaceTracker.lap[eid] += 1;
          playRaceSfx('race-lap');

          if (RaceTracker.lap[eid] >= totalLaps) {
            RaceTracker.finished[eid] = 1;
            RaceTracker.finishTime[eid] = now - race.raceStartTime;
            if (state.hasComponent(eid, PlayerVehicle)) {
              setRaceState({
                phase: 'finished',
                finishTime: now,
              });
              playRaceSfx('race-finish');
            }
          }
        } else if (data.closed && prevFraction < 0.2 && sample.fraction > 0.65) {
          // Reversed over the line — ignore (don't subtract a lap).
        }

        // Monotonic progress = laps completed + fraction of current lap.
        RaceTracker.progress[eid] = RaceTracker.lap[eid] + sample.fraction;
      }
    }
  },

  dispose() {
    resetRaceState();
    metricsCache.clear();
    lastBeepedSecond = -1;
  },
});

export function getPhaseLabel(phase: RacePhase): string {
  switch (phase) {
    case 'idle':
      return 'Get Ready';
    case 'countdown':
      return 'Countdown';
    case 'racing':
      return 'Racing';
    case 'finished':
      return 'Finished';
  }
}
