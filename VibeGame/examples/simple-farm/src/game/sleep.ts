// Sleeping: [J] at the cottage bed rolls the calendar to the next morning.
// Crop growth hangs off the clock's DAY_ADVANCED event — the single day
// boundary — so sleeping past midnight and staying up past midnight grow the
// field identically (one advanceFarmDay per day).

import {
  DAY_ADVANCED,
  FarmGrid,
  advanceFarmDay,
  defineQuery,
  onClockEvent,
  registerInteractionTarget,
  sleepUntilMorning,
  t,
} from 'vibegame';
import type { State } from 'vibegame';
import { showToast } from '../../../shared/src/ui';
import { restoreStamina } from './stamina';

const gridQuery = defineQuery([FarmGrid]);

const BED_ENTITY_NAME = 'farm_home';
const BED_RANGE = 4.5;

export interface DayReport {
  grown: number;
  ripened: number;
  withered: number;
}

/** Advance every farm grid by one day and summarise what happened. */
export function advanceAllGrids(state: State): DayReport {
  const report: DayReport = { grown: 0, ripened: 0, withered: 0 };
  for (const eid of gridQuery(state.world)) {
    const day = advanceFarmDay(state, eid);
    if (!day) continue;
    report.grown += day.grown;
    report.ripened += day.ripened;
    report.withered += day.withered;
  }
  return report;
}

/** [J] at the bed: next morning, full stamina, morning report toast. */
export function sleepAtBed(state: State): void {
  sleepUntilMorning(state);
  restoreStamina();
  showToast(t(state, 'farm.toast.slept'), { durationMs: 2200 });
}

/**
 * Wire the bed prompt + the day-advance hook. Called once from bootstrap;
 * re-registering on a rebuilt world is safe (targets key by entity).
 */
export function registerSleep(state: State): void {
  const bed = state.getEntityByName(BED_ENTITY_NAME);
  if (bed !== null) {
    registerInteractionTarget(state, bed, {
      label: t(state, 'farm.prompt.sleep'),
      key: 'J',
      kind: 'sleep',
      range: BED_RANGE,
    });
  }

  onClockEvent(state, DAY_ADVANCED, () => {
    const report = advanceAllGrids(state);
    if (report.grown > 0 || report.ripened > 0 || report.withered > 0) {
      showToast(
        t(state, 'farm.toast.day')
          .replace('{grown}', String(report.grown))
          .replace('{ripened}', String(report.ripened))
          .replace('{withered}', String(report.withered)),
        { durationMs: 3200 }
      );
    }
  });
}
