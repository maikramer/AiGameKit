import { defineSystem, defineQuery, type State, type System } from '../../core';
import {
  PlayerVehicle,
  PowerUp,
  RaceTracker,
  Track,
  Vehicle,
} from './components';
import { getTrackSpline } from './data';
import { createFrame, type TrackSpline } from './spline';
import { placeVehicleOnTrack } from './vehicle-control';
import { getRaceState } from './race-state';
import { getSoundDef, playSound } from '../audio';

/**
 * Time-trial checkpoints + respawn.
 *
 * The track is split into N evenly spaced checkpoints; the player must
 * cross each in order to record progress. When the car is far off the road
 * or stuck for too long, the controller respawns to the last checkpoint
 * with a small speed penalty. The shield ammo (slot 2, when armed) absorbs
 * one respawn event.
 *
 * Runs in `simulation` so a respawn takes effect *before* the next physics
 * step, and the vehicle-control system reads the freshly placed pose without
 * a one-frame jitter.
 */

const RESPAWN_PENALTY = 6;
/** Off-track time before the respawn triggers. */
const OFF_TRACK_THRESHOLD = 1.4;
/** Stuck time before the respawn triggers (car wedged against a barrier). */
const STUCK_THRESHOLD = 3.5;
/** Min forward progress (m) over the stuck window to count as "moving". */
const STUCK_PROGRESS = 4;
/**
 * Extra lateral distance beyond the road+shoulder edge before the car counts
 * as off-track. The wall already stops the car at `width/2 + shoulder − half
 * width`, so the respawn trigger must be *outside* that hard limit or a car
 * running a wide line would be teleported back every corner.
 */
const OFF_TRACK_MARGIN = 2.5;

const _frame = createFrame();

const playerQuery = defineQuery([PlayerVehicle, Vehicle, RaceTracker]);
const trackQuery = defineQuery([Track]);

function playBanked(key: string): void {
  if (getSoundDef(key)) playSound(key);
}

function respawnToCheckpoint(
  eid: number,
  spline: TrackSpline,
  s: number,
  lateral: number
): void {
  placeVehicleOnTrack(eid, spline, s, lateral);
  Vehicle.speed[eid] = 0;
  Vehicle.lateralSpeed[eid] = 0;
  Vehicle.steer[eid] = 0;
  Vehicle.boosting[eid] = 0;
  Vehicle.boost[eid] = RESPAWN_PENALTY;
  RaceTracker.respawnFlash[eid] = 1;
  RaceTracker.offTrackTimer[eid] = 0;
  RaceTracker.stuckTimer[eid] = 0;
  RaceTracker.stuckS[eid] = s;
  playBanked('race-respawn');
}

export const CheckpointSystem: System = defineSystem({
  name: 'CheckpointSystem',
  group: 'simulation',
  // Deliberately no `before` constraint: the respawn writes Vehicle fields the
  // controller reads next fixed step, and the scheduler forbids constraints
  // across groups (simulation vs fixed).
  update(state: State) {
    const trackEid = trackQuery(state.world)[0];
    if (trackEid === undefined) return;
    const spline = getTrackSpline(trackEid);
    if (!spline) return;
    const count = Track.checkpointCount[trackEid] || 0;
    if (count <= 0) return;
    const length = spline.length;
    const increment = length / count;
    const players = playerQuery(state.world);
    const race = getRaceState();
    if (race.phase !== 'racing') return;
    // Clamp the frame time: a single slow frame (shader compile, GC hiccup)
    // must not count as seconds of being stuck — the car is not wedged, the
    // browser just hiccuped.
    const dt = Math.min(state.time.deltaTime, 0.1);

    for (const eid of players) {
      if (RaceTracker.finished[eid]) continue;
      const s = Vehicle.trackS[eid] ?? 0;
      const last = RaceTracker.lastCheckpointS[eid] ?? 0;
      const idx = RaceTracker.lastCheckpointIndex[eid] ?? 0;
      const nextIdx = idx + 1;
      const nextS = (nextIdx * increment) % length;
      const delta = spline.deltaS(s, last);
      if (delta > 0 && nextIdx <= count && spline.deltaS(s, nextS) > 0) {
        RaceTracker.lastCheckpointIndex[eid] = nextIdx;
        RaceTracker.lastCheckpointS[eid] = nextS;
      }

      // ---- Off-track detection ------------------------------------------
      // A car is off-track only when it is *beyond* the shoulder, i.e. past the
      // barrier line. The lateral limit mirrors the collision clamp in the
      // controller (road half-width + shoulder + wall thickness).
      const sample = spline.sampleAt(s, _frame);
      const lateral = Vehicle.trackLateral[eid] ?? 0;
      const shoulder = Track.shoulder[trackEid] || 0;
      const offTrackLimit = sample.width * 0.5 + shoulder + OFF_TRACK_MARGIN;
      const offTrack = Math.abs(lateral) > offTrackLimit;
      if (offTrack && Vehicle.airborne[eid] === 0) {
        RaceTracker.offTrackTimer[eid] =
          (RaceTracker.offTrackTimer[eid] ?? 0) + dt;
      } else {
        RaceTracker.offTrackTimer[eid] = Math.max(
          0,
          (RaceTracker.offTrackTimer[eid] ?? 0) - dt * 0.5
        );
      }

      // ---- Stuck detection ----------------------------------------------
      // A car grinding along the barrier at walking pace is never "off-track"
      // (the wall holds its lateral inside the limit) but it is not racing.
      // Watch progress along the arc: no forward movement for a few seconds
      // (excluding a car simply parked before the lights) means wedged.
      const progress = spline.deltaS(s, RaceTracker.stuckS[eid] ?? s);
      if (Math.abs(progress) > STUCK_PROGRESS) {
        RaceTracker.stuckTimer[eid] = 0;
        RaceTracker.stuckS[eid] = s;
      } else {
        RaceTracker.stuckTimer[eid] = (RaceTracker.stuckTimer[eid] ?? 0) + dt;
      }

      const needRespawn =
        (RaceTracker.offTrackTimer[eid] ?? 0) > OFF_TRACK_THRESHOLD ||
        (RaceTracker.stuckTimer[eid] ?? 0) > STUCK_THRESHOLD;
      if (needRespawn) {
        // The shield absorbs the respawn only while its latch is still running
        // (the player pressed the key within the last SHIELD_LATCH_S seconds).
        const shieldActive =
          PowerUp.shieldArmed[eid] === 1 && (PowerUp.cd2[eid] ?? 0) > 0;
        if (shieldActive) {
          PowerUp.shieldArmed[eid] = 0;
          PowerUp.cd2[eid] = 0;
          RaceTracker.offTrackTimer[eid] = 0;
          RaceTracker.stuckTimer[eid] = 0;
          playBanked('race-shield');
        } else {
          const lastS = RaceTracker.lastCheckpointS[eid] ?? 0;
          respawnToCheckpoint(eid, spline, lastS, 0);
        }
      }

      // Allow other systems (off-track logs) to read the projected height.
      void sample;
    }
  },
});

/**
 * Reset all per-vehicle checkpoint state (called on each restart).
 */
export function resetCheckpoints(state: State): void {
  for (const eid of playerQuery(state.world)) {
    RaceTracker.lastCheckpointIndex[eid] = 0;
    RaceTracker.lastCheckpointS[eid] = 0;
    RaceTracker.offTrackTimer[eid] = 0;
    RaceTracker.stuckTimer[eid] = 0;
    RaceTracker.stuckS[eid] = Vehicle.trackS[eid] ?? 0;
    RaceTracker.respawnFlash[eid] = 0;
  }
}
