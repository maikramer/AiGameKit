import type { System } from '../../core';
import { defineQuery, TIME_CONSTANTS } from '../../core';
import { Rigidbody, TouchedEvent, TouchEndedEvent } from './components';
import {
  copyRigidbodyToTransforms,
  interpolateTransforms,
  syncRigidbodyToECS,
} from './utils';
import { getPhysicsContext } from './systems';

const touchedEventQuery = defineQuery([TouchedEvent]);
const touchEndedEventQuery = defineQuery([TouchEndedEvent]);

export const CollisionEventCleanupSystem: System = {
  group: 'setup',
  update: (state) => {
    for (const entity of touchedEventQuery(state.world)) {
      state.removeComponent(entity, TouchedEvent);
    }

    for (const entity of touchEndedEventQuery(state.world)) {
      state.removeComponent(entity, TouchEndedEvent);
    }
  },
};

export const PhysicsRapierSyncSystem: System = {
  group: 'fixed',
  update: (state) => {
    const context = getPhysicsContext(state);

    for (const [entity, body] of context.entityToRigidbody) {
      // Corpos dormindo não se moveram: pular evita ~5 chamadas WASM +
      // conversão de euler por corpo parado por step. Kinematic ficam de
      // fora do skip (o estado de sleep deles não garante pose imutável).
      if (body.isSleeping() && !body.isKinematic()) continue;
      if (state.hasComponent(entity, Rigidbody)) {
        syncRigidbodyToECS(entity, body, state);
        copyRigidbodyToTransforms(entity, state);
      }
    }
  },
};

export const PhysicsInterpolationSystem: System = {
  group: 'simulation',
  first: true,
  update: (state) => {
    const alpha =
      state.scheduler.getAccumulator() / TIME_CONSTANTS.FIXED_TIMESTEP;
    interpolateTransforms(state, alpha);
  },
};
