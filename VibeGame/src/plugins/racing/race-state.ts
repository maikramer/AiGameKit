/**
 * Singleton race state — the contract between the race director, the HUD and
 * game code. HUD widgets are plain DOM factories that tick every frame; reading
 * a module singleton keeps their lifecycle independent of entity ids.
 *
 * Phase machine:
 *
 * ```
 * idle ──(track + cars exist)──▶ grid ──(assets ready)──▶ countdown ──▶ racing ──▶ finished
 *   ▲                                                                                │
 *   └──────────────────────────── restartRace() ─────────────────────────────────────┘
 * ```
 *
 * `grid` exists so the cars are parked on the start grid while GLBs stream in;
 * the old build started the clock on frame 1 and the player met a race already
 * 30 seconds old.
 */
export type RacePhase = 'idle' | 'grid' | 'countdown' | 'racing' | 'finished';

/** One row of the end-of-race classification. */
export interface RaceResult {
  entity: number;
  name: string;
  position: number;
  /** Total race time (s); -1 when the car didn't finish. */
  totalTime: number;
  /** Best lap (s); -1 when never set. */
  bestLap: number;
  laps: number;
  isPlayer: boolean;
}

export interface RaceState {
  phase: RacePhase;
  /** Countdown seconds remaining (3 → 0); rendered as 3/2/1/GO. */
  countdown: number;
  /** Player vehicle entity (0 = unset). */
  playerVehicle: number;
  /** Track entity (0 = unset). */
  track: number;
  totalLaps: number;
  /** Number of cars in the race. */
  entrants: number;
  /** Seconds of racing elapsed (excludes the countdown). */
  raceTime: number;
  /** Final classification, filled when the phase turns `finished`. */
  results: RaceResult[];
  /** Incremented by every restart — lets systems reset per-race caches. */
  generation: number;
}

const initial: RaceState = {
  phase: 'idle',
  countdown: 0,
  playerVehicle: 0,
  track: 0,
  totalLaps: 3,
  entrants: 0,
  raceTime: 0,
  results: [],
  generation: 0,
};

let current: RaceState = { ...initial, results: [] };

/** Set by game code when its assets are streamed in; gates the countdown. */
let assetsReady = true;

export function getRaceState(): RaceState {
  return current;
}

export function setRaceState(patch: Partial<RaceState>): void {
  current = { ...current, ...patch };
}

export function resetRaceState(): void {
  current = { ...initial, results: [] };
  assetsReady = true;
}

/** True once the player is allowed to drive. */
export function isRacingActive(): boolean {
  return current.phase === 'racing';
}

/**
 * Hold the race on the grid until the caller says its assets are in.
 * `markRaceReady()` releases it. Games that don't call this start immediately.
 */
export function holdRaceOnGrid(): void {
  assetsReady = false;
}

export function markRaceReady(): void {
  assetsReady = true;
}

export function isRaceReady(): boolean {
  return assetsReady;
}

/**
 * Restart the race: the director re-places every car on the grid and re-runs
 * the countdown on the next frame.
 */
export function restartRace(): void {
  setRaceState({
    phase: 'grid',
    countdown: 0,
    raceTime: 0,
    results: [],
    generation: current.generation + 1,
  });
}
