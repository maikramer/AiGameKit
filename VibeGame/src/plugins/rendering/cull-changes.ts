import type { State } from '../../core';

/**
 * Entities whose `DistanceCull.culled` flipped since the last consumer ran.
 *
 * Culling is edge-triggered — `DistanceCullSystem` already knows the exact
 * frame a prop crosses the threshold — but consumers used to *poll* for it.
 * That is fine for a handful of props and quadratic misery for a world of
 * instanced vegetation: the instancing pool ended up re-examining tens of
 * thousands of static slots just in case one of them had been culled.
 * Publishing the flips instead turns that poll into a handful of updates.
 */
const changedByState = new WeakMap<State, Set<number>>();

/** Record a cull-state flip (called by `DistanceCullSystem`). */
export function markDistanceCullChanged(state: State, entity: number): void {
  let set = changedByState.get(state);
  if (!set) {
    set = new Set();
    changedByState.set(state, set);
  }
  set.add(entity);
}

/**
 * Read the pending flips. The set is live — consumers must not hold it across
 * frames; call {@link clearDistanceCullChanges} once every consumer has run.
 */
export function getDistanceCullChanges(state: State): ReadonlySet<number> {
  return changedByState.get(state) ?? EMPTY;
}

const EMPTY: ReadonlySet<number> = new Set();

/** Drop the pending flips (end of frame). */
export function clearDistanceCullChanges(state: State): void {
  changedByState.get(state)?.clear();
}
