import { defineQuery, type State } from '../../core';
import { GameClock } from './components';
import { SEASON_NAMES } from './calendar';
import { advanceClockMinutes } from './systems';

const clockQuery = defineQuery([GameClock]);

/** First GameClock entity, or 0 when the scene has none. */
export function getClockEntity(state: State): number {
  const clocks = clockQuery(state.world);
  return clocks.length > 0 ? clocks[0] : 0;
}

export interface TimeOfDay {
  minuteOfDay: number;
  day: number;
  season: number;
  seasonName: string;
  year: number;
  daysPerSeason: number;
  paused: boolean;
}

export function getTimeOfDay(state: State): TimeOfDay | null {
  const eid = getClockEntity(state);
  if (!eid) return null;
  return {
    minuteOfDay: GameClock.minuteOfDay[eid],
    day: GameClock.day[eid],
    season: GameClock.season[eid],
    seasonName: SEASON_NAMES[GameClock.season[eid]] ?? 'spring',
    year: GameClock.year[eid],
    daysPerSeason: GameClock.daysPerSeason[eid],
    paused: GameClock.paused[eid] === 1,
  };
}

/** Calendar speed: in-game minutes per real second. */
export function setClockScale(state: State, scale: number): void {
  const eid = getClockEntity(state);
  if (eid) GameClock.minutesPerRealSecond[eid] = Math.max(0, scale);
}

export function setClockPaused(state: State, paused: boolean): void {
  const eid = getClockEntity(state);
  if (eid) GameClock.paused[eid] = paused ? 1 : 0;
}

/**
 * Roll the calendar forward exactly one day (same wall time). Sleep flows
 * should prefer {@link sleepUntilMorning}.
 */
export function advanceGameDay(state: State): string[] {
  const eid = getClockEntity(state);
  return eid ? advanceClockMinutes(state, eid, 1440) : [];
}

/**
 * Fast-forward to the next wake minute (default 6:00) — the "go to bed"
 * button. Passes through midnight via the same rollover pipeline, so day/
 * season/year events fire exactly as they would awake.
 */
export function sleepUntilMorning(state: State): string[] {
  const eid = getClockEntity(state);
  if (!eid) return [];
  const wake = GameClock.wakeMinute[eid];
  let minutes = wake - GameClock.minuteOfDay[eid];
  if (minutes <= 0) minutes += 1440; // always at least into the next morning
  return advanceClockMinutes(state, eid, minutes);
}

/** "06:42" — wall clock rendering (the HUD has its own formatTime for durations). */
export function formatClock(state: State): string {
  const eid = getClockEntity(state);
  if (!eid) return '--:--';
  return formatMinute(GameClock.minuteOfDay[eid]);
}

export function formatMinute(minuteOfDay: number): string {
  const m = Math.floor(((minuteOfDay % 1440) + 1440) % 1440);
  const h = Math.floor(m / 60);
  const mm = m % 60;
  return `${String(h).padStart(2, '0')}:${String(mm).padStart(2, '0')}`;
}
