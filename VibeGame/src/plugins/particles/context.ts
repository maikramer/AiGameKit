import type { Object3D } from 'three';
import type { BatchedRenderer } from 'three.quarks';
import type { State } from '../../core';

export interface ParticlesContext {
  batch: BatchedRenderer | null;
  /** Object3D wrapper por entidade (ParticleEmitter three.quarks) */
  roots: Map<number, Object3D>;
}

const stateToParticles = new WeakMap<State, ParticlesContext>();

export function getParticlesContext(state: State): ParticlesContext {
  let ctx = stateToParticles.get(state);
  if (!ctx) {
    ctx = { batch: null, roots: new Map() };
    stateToParticles.set(state, ctx);
  }
  return ctx;
}
