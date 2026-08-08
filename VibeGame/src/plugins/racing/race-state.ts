/**
 * Singleton race state — the bridge between the race systems and the HUD widgets.
 * HUD widgets are pure DOM factories updated every frame; they read this module
 * instead of threading race state through ECS queries (which would couple widget
 * lifecycles to entity ids). Mirrors how the timer widget reads `state.time`.
 *
 * Phase machine: COUNTDOWN -> RACING -> FINISHED. The countdown lives here so any
 * system (vehicle control, HUD, audio) can gate on it without re-deriving it.
 */
export type RacePhase = 'idle' | 'countdown' | 'racing' | 'finished';

export interface RaceState {
  phase: RacePhase;
  /** Countdown: 3.0 -> 0.0 counts down; we render ceil() as 3/2/1 and 0 as GO. */
  countdown: number;
  /** The player vehicle entity (0 if unset). */
  playerVehicle: number;
  /** The track entity (0 if unset). */
  track: number;
  /** Total laps configured for the race. */
  totalLaps: number;
  /** Realtime (state.time.realtimeSinceStartup) the racing phase began. */
  raceStartTime: number;
  /** Realtime the player finished. */
  finishTime: number;
}

const initial: RaceState = {
  phase: 'idle',
  countdown: 0,
  playerVehicle: 0,
  track: 0,
  totalLaps: 3,
  raceStartTime: 0,
  finishTime: 0,
};

let current: RaceState = { ...initial };

export function getRaceState(): RaceState {
  return current;
}

export function setRaceState(patch: Partial<RaceState>): void {
  current = { ...current, ...patch };
}

export function resetRaceState(): void {
  current = { ...initial };
}

/** Convenience: has the race reached the point where the player can drive? */
export function isRacingActive(): boolean {
  return current.phase === 'racing';
}
