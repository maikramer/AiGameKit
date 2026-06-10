import type { State } from '../../core';
import { Transform, WorldTransform } from '../transforms/components';
import { ParticleEmitter } from './components';
import { presetIndex } from './presets';

export interface ParticleBurstOptions {
  x: number;
  y: number;
  z: number;
  /** Preset name (fire, explosion, sparks…); default 'explosion'. */
  preset?: string;
  count?: number;
  duration?: number;
}

/**
 * Spawn a one-shot, self-destroying particle burst at a world position.
 * Returns the emitter entity id.
 */
export function spawnParticleBurst(
  state: State,
  options: ParticleBurstOptions
): number {
  const eid = state.createEntity();

  // addComponent zeroes every field — restore identity scale/rotation or the
  // emitter's world matrix degenerates.
  state.addComponent(eid, Transform);
  Transform.posX[eid] = options.x;
  Transform.posY[eid] = options.y;
  Transform.posZ[eid] = options.z;
  Transform.scaleX[eid] = 1;
  Transform.scaleY[eid] = 1;
  Transform.scaleZ[eid] = 1;
  Transform.rotW[eid] = 1;
  Transform.dirty[eid] = 1;

  // Seed WorldTransform directly so the time-0 burst fires in place instead
  // of at the origin while the hierarchy system catches up.
  state.addComponent(eid, WorldTransform);
  WorldTransform.posX[eid] = options.x;
  WorldTransform.posY[eid] = options.y;
  WorldTransform.posZ[eid] = options.z;
  WorldTransform.scaleX[eid] = 1;
  WorldTransform.scaleY[eid] = 1;
  WorldTransform.scaleZ[eid] = 1;
  WorldTransform.rotW[eid] = 1;

  state.addComponent(eid, ParticleEmitter);
  ParticleEmitter.active[eid] = 1;
  ParticleEmitter.preset[eid] = presetIndex(options.preset ?? 'explosion');
  ParticleEmitter.burst[eid] = 1;
  ParticleEmitter.looping[eid] = 0;
  ParticleEmitter.burstCount[eid] = options.count ?? 60;
  ParticleEmitter.duration[eid] = options.duration ?? 0.5;
  ParticleEmitter.worldSpace[eid] = 0;

  return eid;
}
