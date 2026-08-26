import type { State } from '../../core';
import { GameClock } from './components';
import { getClockEntity } from './api';
import {
  registerGlobalSaveSerializer,
  type GlobalSaveSerializer,
  type SerializedKind,
} from '../save-load/serializer-registry';

/**
 * The clock persists as a global: day/season/year/minute at save time. The
 * rest (arc shaping, step) comes from the recipe on load.
 */
interface ClockSave {
  v: 1;
  minuteOfDay: number;
  day: number;
  season: number;
  year: number;
}

const clockSerializer: GlobalSaveSerializer = {
  serialize(state: State): SerializedKind {
    const eid = getClockEntity(state);
    if (!eid) return null;
    return {
      v: 1,
      minuteOfDay: GameClock.minuteOfDay[eid],
      day: GameClock.day[eid],
      season: GameClock.season[eid],
      year: GameClock.year[eid],
    } satisfies ClockSave;
  },
  deserialize(state: State, data: SerializedKind): void {
    const eid = getClockEntity(state);
    if (!eid) return;
    const save = data as ClockSave;
    if (!save || save.v !== 1) return;
    GameClock.minuteOfDay[eid] = save.minuteOfDay;
    GameClock.day[eid] = save.day;
    GameClock.season[eid] = save.season;
    GameClock.year[eid] = save.year;
  },
};

export function registerClockSerializer(state: State): void {
  registerGlobalSaveSerializer(state, 'daycycle', clockSerializer);
}
