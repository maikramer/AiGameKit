import type { State } from '../../core';
import { Transform, WorldTransform } from '../transforms/components';
import { FloatingText } from './components';

const textByState = new WeakMap<State, Map<number, string>>();

export function setFloatingTextString(
  state: State,
  entity: number,
  text: string
): void {
  let m = textByState.get(state);
  if (!m) {
    m = new Map();
    textByState.set(state, m);
  }
  m.set(entity, text);
}

export function getFloatingTextString(
  state: State,
  entity: number
): string | undefined {
  return textByState.get(state)?.get(entity);
}

export function deleteFloatingTextString(state: State, entity: number): void {
  textByState.get(state)?.delete(entity);
}

export interface FloatingTextOptions {
  x: number;
  y: number;
  z: number;
  /** 0xRRGGBB; default white. */
  color?: number;
  /** Font size in world meters (default 0.35). */
  size?: number;
  /** Seconds before the text fades out and is destroyed (default 1.4). */
  duration?: number;
  /** Upward drift in m/s (default 0.9). */
  riseSpeed?: number;
}

/**
 * Spawn a self-destroying billboarded text at a world position — pickups,
 * damage numbers, quest pings. Returns the entity id.
 */
export function spawnFloatingText(
  state: State,
  text: string,
  options: FloatingTextOptions
): number {
  const eid = state.createEntity();

  // addComponent zeroes every field — restore identity scale/rotation.
  state.addComponent(eid, Transform);
  Transform.posX[eid] = options.x;
  Transform.posY[eid] = options.y;
  Transform.posZ[eid] = options.z;
  Transform.scaleX[eid] = 1;
  Transform.scaleY[eid] = 1;
  Transform.scaleZ[eid] = 1;
  Transform.rotW[eid] = 1;
  Transform.dirty[eid] = 1;

  state.addComponent(eid, WorldTransform);
  WorldTransform.posX[eid] = options.x;
  WorldTransform.posY[eid] = options.y;
  WorldTransform.posZ[eid] = options.z;
  WorldTransform.scaleX[eid] = 1;
  WorldTransform.scaleY[eid] = 1;
  WorldTransform.scaleZ[eid] = 1;
  WorldTransform.rotW[eid] = 1;

  state.addComponent(eid, FloatingText);
  FloatingText.elapsed[eid] = 0;
  FloatingText.duration[eid] = options.duration ?? 1.4;
  FloatingText.riseSpeed[eid] = options.riseSpeed ?? 0.9;
  FloatingText.size[eid] = options.size ?? 0.35;
  const color = options.color ?? 0xffffff;
  FloatingText.colorR[eid] = ((color >> 16) & 0xff) / 255;
  FloatingText.colorG[eid] = ((color >> 8) & 0xff) / 255;
  FloatingText.colorB[eid] = (color & 0xff) / 255;

  setFloatingTextString(state, eid, text);
  return eid;
}
