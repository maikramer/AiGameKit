import type { State } from '../../core';
import { Transform } from '../transforms';

/**
 * Player gesture played when activating an interaction target (e.g. KeyF).
 * - `'gather'`: bend-down collect / pick-up (mushroom, ground loot).
 * - `'none'`: no long override — portals, chests, readables, merchants, etc.
 * Omitting `gesture` defaults to `'none'`.
 */
export type InteractionGesture = 'gather' | 'none';

export interface InteractionTarget {
  label?: string;
  i18nKey?: string;
  kind?: string;
  key?: string;
  /**
   * Range in metres for this target only, overriding the widget default.
   *
   * Needed whenever one object's action reaches further than the rest: a hint
   * that only appears at 4.5 m for something you can already do at 10 m reads
   * as an unresponsive object, and the player walks away before it lights up.
   */
  range?: number;
  /** Player body gesture on activate. Default `'none'`. */
  gesture?: InteractionGesture;
}

export interface NearestInteraction {
  eid: number;
  info: InteractionTarget;
  distSq: number;
}

const DEFAULT_PROMPT_RANGE = 4.5;

const stateToTargets = new WeakMap<State, Map<number, InteractionTarget>>();

function targetMap(state: State): Map<number, InteractionTarget> {
  let m = stateToTargets.get(state);
  if (!m) {
    m = new Map();
    stateToTargets.set(state, m);
  }
  return m;
}

export function registerInteractionTarget(
  state: State,
  eid: number,
  info: InteractionTarget
): void {
  // Key normalizada uma única vez: o sweep por frame compara strings diretas
  // (milhares de targets — regex por target por frame era custo visível).
  if (info.key !== undefined && info.key !== null) {
    info = { ...info, key: normalizePromptKey(info.key) };
  }
  targetMap(state).set(eid, info);
}

export function unregisterInteractionTarget(state: State, eid: number): void {
  targetMap(state).delete(eid);
}

export function getInteractionTargets(
  state: State
): ReadonlyMap<number, InteractionTarget> {
  return targetMap(state);
}

/** Normalize `'KeyF'` / `'f'` / `'F'` to a single uppercase letter code. */
export function normalizePromptKey(key: string): string {
  const trimmed = key.trim();
  if (/^Key[A-Za-z]$/i.test(trimmed)) return trimmed.slice(3).toUpperCase();
  return trimmed.toUpperCase();
}

/**
 * Nearest registered interactable within range of `(px, pz)`.
 * When `key` is set, only targets whose prompt key matches (default `'F'`).
 */
export function findNearestInteractionTarget(
  state: State,
  px: number,
  pz: number,
  opts?: { key?: string; defaultRange?: number }
): NearestInteraction | null {
  const wantKey = normalizePromptKey(opts?.key ?? 'F');
  const defaultRange = opts?.defaultRange ?? DEFAULT_PROMPT_RANGE;

  let best: NearestInteraction | null = null;

  for (const [eid, info] of targetMap(state)) {
    if (!state.exists(eid)) continue;
    // info.key já vem normalizado do registo (ou default 'F').
    if ((info.key ?? 'F') !== wantKey) continue;

    const range = info.range ?? defaultRange;
    const rangeSq = range * range;
    const dx = Transform.posX[eid] - px;
    const dz = Transform.posZ[eid] - pz;
    const distSq = dx * dx + dz * dz;
    if (distSq > rangeSq) continue;
    if (!best || distSq < best.distSq) {
      best = { eid, info, distSq };
    }
  }

  return best;
}

/** Effective gesture for a target (`gesture` omitted ⇒ `'none'`). */
export function resolveInteractionGesture(
  info: InteractionTarget | null | undefined
): InteractionGesture {
  return info?.gesture === 'gather' ? 'gather' : 'none';
}
