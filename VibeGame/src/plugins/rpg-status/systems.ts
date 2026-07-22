import type { State, System } from '../../core';
import { defineSystem } from '../../core';
import { getEventBus } from '../rpg-core';
import {
  drainPendingEvents,
  ensureDeathSubscription,
  tickStatusEffects,
} from './components';

export const StatusEffectTickSystem: System = defineSystem({
  name: 'StatusEffectTickSystem',
  group: 'simulation',
  update(state: State): void {
    ensureDeathSubscription(state);
    const dt = state.time.deltaTime;
    if (dt > 0) tickStatusEffects(state, dt);
  },
});

export const StatusEffectEventBridgeSystem: System = defineSystem({
  name: 'StatusEffectEventBridgeSystem',
  group: 'simulation',
  update(state: State): void {
    const events = drainPendingEvents(state);
    if (events.length === 0) return;
    const bus = getEventBus(state);
    for (const { event, payload } of events) {
      bus.emit(event, payload);
    }
  },
});
