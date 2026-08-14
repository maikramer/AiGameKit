import { Vector3 } from 'three';
import type { State } from '../../core';
import { getMainThreeCamera } from '../rendering/utils';
import { Transform, WorldTransform } from '../transforms/components';
import { FloatingText } from './components';
import { claimStackSlot } from './stacking';

const _proj = new Vector3();

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
  /**
   * Vertical-stack key: texts sharing a key stack upward instead of overlapping.
   * When set, the spawn Y is offset by `claimStackSlot(key)` (0, gap, 2*gap, …).
   */
  stackKey?: string;
  /**
   * Anchor Y for the stack; when set, the spawn Y becomes `stackBaseY +
   * yOffset` (rather than `options.y + yOffset`). Use to align a stack across
   * callers that pass different raw `y` values.
   */
  stackBaseY?: number;
  /** Vertical gap between stacked texts in world meters (default 0.5). */
  stackGap?: number;
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
 *
 * When `stackKey` is set, the spawn Y is offset vertically by `claimStackSlot`
 * so texts sharing the key stack upward instead of overlapping (damage, loot,
 * popups on the same target).
 */
export function spawnFloatingText(
  state: State,
  text: string,
  options: FloatingTextOptions
): number {
  const eid = state.createEntity();
  const space: FloatingTextSpace = options.space ?? 'world';

  // Resolve the spawn Y, applying a vertical stack offset when requested. In
  // screen mode the gap is interpreted as CSS pixels; in world mode as meters.
  let spawnY = options.y;
  if (options.stackKey) {
    const duration = options.duration ?? 1.4;
    // Screen mode works in px; convert the (meter-typed) gap to px for stacking.
    const gap =
      space === 'screen'
        ? (options.stackGap ?? 0.5) * 100
        : (options.stackGap ?? 0.5);
    const slot = claimStackSlot(state, options.stackKey, eid, duration, gap);
    spawnY =
      options.stackBaseY !== undefined
        ? options.stackBaseY + slot.yOffset
        : options.y + slot.yOffset;
  }

  if (space === 'world') {
    const z = options.z ?? 0;
    state.addComponent(eid, Transform);
    Transform.posX[eid] = options.x;
    Transform.posY[eid] = spawnY;
    Transform.posZ[eid] = z;
    Transform.scaleX[eid] = 1;
    Transform.scaleY[eid] = 1;
    Transform.scaleZ[eid] = 1;
    Transform.rotW[eid] = 1;
    Transform.dirty[eid] = 1;
    state.addComponent(eid, WorldTransform);
    WorldTransform.posX[eid] = options.x;
    WorldTransform.posY[eid] = spawnY;
    WorldTransform.posZ[eid] = z;
    WorldTransform.scaleX[eid] = 1;
    WorldTransform.scaleY[eid] = 1;
    WorldTransform.scaleZ[eid] = 1;
    WorldTransform.rotW[eid] = 1;
  } else if (options.stackKey) {
    // Screen mode: fold the stacked offset into screenY so the pool update
    // (which reads screenY) keeps the text at the right place.
    applyCommon(state, eid, options, space);
    FloatingText.screenY[eid] = spawnY;
    setFloatingTextString(state, eid, text);
    return eid;
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

export interface DamageNumberOptions {
  /** World position of the hit (usually entity chest). */
  x: number;
  y: number;
  z: number;
  /** Rounded damage amount shown as text. */
  amount: number;
  /** True when the hit is on the hero (red "−N"); false = enemy hit. */
  onHero?: boolean;
  /** Crit / big-hit styling (bigger + hotter). */
  crit?: boolean;
  /** Stack key so multi-hits on the same target don't overlap. */
  stackKey?: string;
}

/**
 * Action-RPG damage popup: project world → screen and spawn a DOM float.
 * Falls back to world-space troika text when no camera / DOM is available.
 */
export function spawnDamageNumber(
  state: State,
  options: DamageNumberOptions
): number {
  const amount = Math.max(0, Math.round(options.amount));
  const onHero = options.onHero === true;
  const crit = options.crit === true && !onHero;
  const text = onHero ? `−${amount}` : crit ? `${amount}!` : `${amount}`;
  const color = onHero ? '#ff5a5a' : crit ? '#ff8a2a' : '#fff4b0';

  const cam = getMainThreeCamera(state) as
    { project: (v: Vector3) => Vector3 } | undefined;
  const hasDom =
    typeof document !== 'undefined' &&
    typeof window !== 'undefined' &&
    window.innerWidth > 0;

  if (cam && typeof cam.project === 'function' && hasDom) {
    const v = _proj.set(options.x, options.y, options.z);
    cam.project(v);
    if (v.z < 1) {
      const sx = (v.x * 0.5 + 0.5) * window.innerWidth;
      const sy = (-v.y * 0.5 + 0.5) * window.innerHeight;
      return spawnFloatingText(state, text, {
        x: sx + (Math.random() * 18 - 9),
        y: sy - 28,
        color,
        duration: crit ? 1.25 : 1.0,
        riseSpeed: crit ? 70 : 55,
        fontSizePx: onHero ? 22 : crit ? 34 : 26,
        driftX: Math.random() * 40 - 20,
        crit,
        space: 'screen',
        stackKey: options.stackKey,
        stackBaseY: sy - 28,
        stackGap: 0.22,
      });
    }
  }

  return spawnFloatingText(state, text, {
    x: options.x,
    y: options.y,
    z: options.z,
    color,
    size: onHero ? 0.5 : crit ? 0.72 : 0.52,
    duration: crit ? 1.3 : 1.0,
    stackKey: options.stackKey,
    stackBaseY: options.y,
    stackGap: 0.45,
  });
}
