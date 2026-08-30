// Game-registered debug buttons surfaced in the profiler panel's "Extras"
// tab. Engines stay generic: games call `registerProfilerExtra` with a label
// and a click handler (e.g. toggling an in-game tuning tool), and the panel
// lists them as buttons next to the built-in tabs.
import type { State } from '../../core';

export interface ProfilerExtra {
  /** Stable id (dedupe/replace on re-register, e.g. HMR). */
  id: string;
  /** Button label (short; emojis welcome). */
  label: string;
  /** Optional one-line description rendered under the buttons. */
  description?: string;
  onClick: () => void;
}

const extrasByState = new WeakMap<State, ProfilerExtra[]>();

/** Register (or replace, by id) a debug button for the profiler Extras tab. */
export function registerProfilerExtra(
  state: State,
  extra: ProfilerExtra
): void {
  let list = extrasByState.get(state);
  if (!list) {
    list = [];
    extrasByState.set(state, list);
  }
  const idx = list.findIndex((e) => e.id === extra.id);
  if (idx >= 0) list[idx] = extra;
  else list.push(extra);
}

export function unregisterProfilerExtra(state: State, id: string): void {
  const list = extrasByState.get(state);
  if (!list) return;
  const idx = list.findIndex((e) => e.id === id);
  if (idx >= 0) list.splice(idx, 1);
}

export function getProfilerExtras(state: State): ProfilerExtra[] {
  return extrasByState.get(state) ?? [];
}
