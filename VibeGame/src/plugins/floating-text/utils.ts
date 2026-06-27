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

export type FloatingTextSpace = 'world' | 'screen';

export interface FloatingTextOptions {
  x: number;
  y: number;
  z?: number;
  /** 0xRRGGBB or '#rrggbb' / '#rgb'; default white. */
  color?: number | string;
  /** Font size in world meters (world mode, default 0.35). */
  size?: number;
  /** Seconds before the text fades out and is destroyed (default 1.4). */
  duration?: number;
  /** Upward drift. World mode: m/s (default 0.9). Screen mode: px/s (default 50). */
  riseSpeed?: number;
  /** Rendering target: 'world' (troika 3D, default) or 'screen' (DOM pool). */
  space?: FloatingTextSpace;
  /** Screen mode: font size in CSS pixels (default 20, or 26 when crit). */
  fontSizePx?: number;
  /** Screen mode: signed horizontal drift in px (default random in [-17, 17]). */
  driftX?: number;
  /** Screen mode: crit flag (bigger font + red-orange tint). */
  crit?: boolean;
}

export interface ScreenFloatingTextOptions {
  x: number;
  y: number;
  color?: number | string;
  duration?: number;
  riseSpeed?: number;
  fontSizePx?: number;
  driftX?: number;
  crit?: boolean;
}

function parseColorHex(input: number | string | undefined): {
  r: number;
  g: number;
  b: number;
} {
  if (input === undefined) return { r: 1, g: 1, b: 1 };
  if (typeof input === 'number') {
    return {
      r: ((input >> 16) & 0xff) / 255,
      g: ((input >> 8) & 0xff) / 255,
      b: (input & 0xff) / 255,
    };
  }
  let hex = input.trim();
  if (hex.startsWith('#')) hex = hex.slice(1);
  if (hex.length === 3) {
    hex = hex
      .split('')
      .map((c) => c + c)
      .join('');
  }
  const num = parseInt(hex, 16);
  if (!Number.isFinite(num)) return { r: 1, g: 1, b: 1 };
  return {
    r: ((num >> 16) & 0xff) / 255,
    g: ((num >> 8) & 0xff) / 255,
    b: (num & 0xff) / 255,
  };
}

function applyCommon(
  state: State,
  eid: number,
  options: FloatingTextOptions,
  space: FloatingTextSpace
): void {
  state.addComponent(eid, FloatingText);
  FloatingText.elapsed[eid] = 0;
  FloatingText.duration[eid] = options.duration ?? 1.4;
  FloatingText.space[eid] = space === 'screen' ? 1 : 0;
  const { r, g, b } = parseColorHex(options.color);
  FloatingText.colorR[eid] = r;
  FloatingText.colorG[eid] = g;
  FloatingText.colorB[eid] = b;
  if (space === 'screen') {
    FloatingText.riseSpeed[eid] = options.riseSpeed ?? 50;
    FloatingText.fontSizePx[eid] = options.fontSizePx ?? 0;
    FloatingText.crit[eid] = options.crit ? 1 : 0;
    FloatingText.driftX[eid] = options.driftX ?? (Math.random() * 2 - 1) * 17;
    FloatingText.screenX[eid] = options.x;
    FloatingText.screenY[eid] = options.y;
  } else {
    FloatingText.riseSpeed[eid] = options.riseSpeed ?? 0.9;
    FloatingText.size[eid] = options.size ?? 0.35;
  }
}

/**
 * Spawn a self-destroying floating text. In world mode (default) it is a
 * billboarded troika 3D glyph at `x/y/z`; in screen mode (`space: 'screen'`)
 * it is a DOM span recycled through the HudScreenLayer pool anchored at
 * screen pixel `x/y`. Returns the new entity id.
 */
export function spawnFloatingText(
  state: State,
  text: string,
  options: FloatingTextOptions
): number {
  const eid = state.createEntity();
  const space: FloatingTextSpace = options.space ?? 'world';

  if (space === 'world') {
    const z = options.z ?? 0;
    state.addComponent(eid, Transform);
    Transform.posX[eid] = options.x;
    Transform.posY[eid] = options.y;
    Transform.posZ[eid] = z;
    Transform.scaleX[eid] = 1;
    Transform.scaleY[eid] = 1;
    Transform.scaleZ[eid] = 1;
    Transform.rotW[eid] = 1;
    Transform.dirty[eid] = 1;
    state.addComponent(eid, WorldTransform);
    WorldTransform.posX[eid] = options.x;
    WorldTransform.posY[eid] = options.y;
    WorldTransform.posZ[eid] = z;
    WorldTransform.scaleX[eid] = 1;
    WorldTransform.scaleY[eid] = 1;
    WorldTransform.scaleZ[eid] = 1;
    WorldTransform.rotW[eid] = 1;
  }

  applyCommon(state, eid, options, space);
  setFloatingTextString(state, eid, text);
  return eid;
}

/**
 * Spawn a screen-space DOM floating text in the HudScreenLayer at pixel
 * coordinates `(x, y)`. Convenience wrapper around `spawnFloatingText` with
 * `space: 'screen'`. The span is recycled via a fixed-size pool (default 32).
 */
export function spawnFloatingTextScreen(
  state: State,
  text: string,
  options: ScreenFloatingTextOptions
): number {
  return spawnFloatingText(state, text, {
    x: options.x,
    y: options.y,
    color: options.color,
    duration: options.duration,
    riseSpeed: options.riseSpeed,
    fontSizePx: options.fontSizePx,
    driftX: options.driftX,
    crit: options.crit,
    space: 'screen',
  });
}
