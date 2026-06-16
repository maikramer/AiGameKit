import type { State, System } from '../../core';
import { getEventBus } from '../rpg-core';
import { drainPendingEvents } from './components';

export const ProgressionEventBridgeSystem: System = {
  group: 'simulation',
  update(state: State): void {
    const events = drainPendingEvents(state);
    if (events.length === 0) return;
    const bus = getEventBus(state);
    for (const { event, payload } of events) {
      bus.emit(event, payload);
    }
  },
};
