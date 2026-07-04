import type { State } from '../../core';

/**
 * Best-effort vertical stacking for floating text sharing the same `stackKey`
 * (e.g. all feedback spawned at one prop: damage, hit icon, popup, +1 loot).
 * Without this every text spawned at the same point overlaps exactly.
 *
 * Each `claimStackSlot` returns the next yOffset (0, gap, 2*gap, …) for that
 * key. Entries expire `duration + margin` seconds after the spawn so the stack
 * resets once the texts have faded; a hard cap keeps the offset from climbing
 * unbounded on very busy targets.
 */

const MAX_LIVE_SLOTS = 6;
const EXPIRY_MARGIN_SEC = 0.2;
const DEFAULT_GAP = 0.5;

export interface StackEntry {
  eid: number;
  yOffset: number;
  expiresAt: number;
}

export interface StackSlot {
  yOffset: number;
  eid: number;
}

const stacksByState = new WeakMap<State, Map<string, StackEntry[]>>();

function getStacks(state: State): Map<string, StackEntry[]> {
  let m = stacksByState.get(state);
  if (!m) {
    m = new Map();
    stacksByState.set(state, m);
  }
  return m;
}

function dropExpired(entries: StackEntry[], now: number): void {
  for (let i = entries.length - 1; i >= 0; i--) {
    if (entries[i].expiresAt <= now) entries.splice(i, 1);
  }
}

/**
 * Claim the next vertical slot for `key`. Returns `{ yOffset, eid }` where
 * `yOffset` is the offset to add to the spawn Y (world meters) and `eid` is
 * the new floating-text entity, registered for cleanup when it dies.
 */
export function claimStackSlot(
  state: State,
  key: string,
  eid: number,
  duration: number,
  gap: number = DEFAULT_GAP
): StackSlot {
  const now = state.time.elapsed;
  const stacks = getStacks(state);
  const entries = stacks.get(key) ?? [];
  dropExpired(entries, now);

  let yOffset = 0;
  if (entries.length > 0) {
    let maxY = 0;
    for (const e of entries) if (e.yOffset > maxY) maxY = e.yOffset;
    yOffset = maxY + gap;
  }

  // Cap live entries: when at capacity, replace the soonest-to-expire slot so
  // the stack recycles instead of climbing forever on a spammed target.
  if (entries.length >= MAX_LIVE_SLOTS) {
    let soonestIdx = 0;
    let soonestAt = Infinity;
    for (let i = 0; i < entries.length; i++) {
      if (entries[i].expiresAt < soonestAt) {
        soonestAt = entries[i].expiresAt;
        soonestIdx = i;
      }
    }
    entries.splice(soonestIdx, 1);
  }

  const entry: StackEntry = {
    eid,
    yOffset,
    expiresAt: now + duration + EXPIRY_MARGIN_SEC,
  };
  entries.push(entry);
  stacks.set(key, entries);

  // Release the slot early if the text is destroyed before its lifetime.
  state.onDestroy(eid, () => {
    const list = stacks.get(key);
    if (!list) return;
    const idx = list.findIndex((e) => e.eid === eid);
    if (idx >= 0) list.splice(idx, 1);
  });

  return { yOffset, eid };
}

/** Drop all stack state for a state (used by tests / HMR dispose). */
export function clearFloatingTextStacks(state: State): void {
  stacksByState.get(state)?.clear();
}
