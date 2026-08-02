import type { System } from '../../core';
import { defineSystem, defineQuery, TIME_CONSTANTS } from '../../core';
import {
  BodyType,
  InterpolatedTransform,
  Rigidbody,
  TouchedEvent,
  TouchEndedEvent,
} from './components';
import { Transform, WorldTransform } from '../transforms/components';
import {
  copyRigidbodyToTransforms,
  interpolateTransforms,
  syncRigidbodyToECS,
} from './utils';
import { getPhysicsContext } from './systems';

const touchedEventQuery = defineQuery([TouchedEvent]);
const touchEndedEventQuery = defineQuery([TouchEndedEvent]);

export const CollisionEventCleanupSystem: System = defineSystem({
  name: 'CollisionEventCleanupSystem',
  group: 'setup',
  update: (state) => {
    for (const entity of touchedEventQuery(state.world)) {
      state.removeComponent(entity, TouchedEvent);
    }

    for (const entity of touchEndedEventQuery(state.world)) {
      state.removeComponent(entity, TouchEndedEvent);
    }
  },
});

export const PhysicsRapierSyncSystem: System = defineSystem({
  name: 'PhysicsRapierSyncSystem',
  group: 'fixed',
  update: (state) => {
    const context = getPhysicsContext(state);

    for (const [entity, body] of context.entityToRigidbody) {
      // Corpos fixos nunca se movem: skip puro em JS. Evita a chamada WASM
      // `isSleeping()` por corpo parado por step — com milhares de colisores
      // fixos (coletáveis pré-calculados) eram ~2400 chamadas WASM/frame.
      if (
        !state.hasComponent(entity, Rigidbody) ||
        Rigidbody.type[entity] === BodyType.Fixed
      ) {
        continue;
      }
      // Corpos dormindo não se moveram: pular evita ~5 chamadas WASM +
      // conversão de euler por corpo parado por step. Kinematic ficam de
      // fora do skip (o estado de sleep deles não garante pose imutável).
      if (body.isSleeping() && !body.isKinematic()) continue;
      // copyRigidbodyToTransforms requires Transform + WorldTransform +
      // InterpolatedTransform in addition to Rigidbody. Short-lived physics
      // entities (projectiles) can lose Transform mid-destroy while their
      // Rapier body is still in entityToRigidbody for a frame; skip those
      // instead of throwing, which otherwise spams the console and aborts the
      // fixed-step loop.
      if (
        !state.hasComponent(entity, Transform) ||
        !state.hasComponent(entity, WorldTransform) ||
        !state.hasComponent(entity, InterpolatedTransform)
      ) {
        continue;
      }
      syncRigidbodyToECS(entity, body, state);
      copyRigidbodyToTransforms(entity, state);
    }
  },
});

export const PhysicsInterpolationSystem: System = defineSystem({
  name: 'PhysicsInterpolationSystem',
  group: 'simulation',
  first: true,
  update: (state) => {
    const alpha =
      state.scheduler.getAccumulator() / TIME_CONSTANTS.FIXED_TIMESTEP;
    interpolateTransforms(state, alpha);
  },
});
