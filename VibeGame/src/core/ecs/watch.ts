import { defineSystem } from '../profiler';
import type { State } from './state';
import type { System } from './types';
import { defineQuery } from './query';
import { logger } from '../utils/logger';

export interface QueryWatcherHandlers {
  /** Entity now matches every component in the query. */
  onAdded?: (eid: number) => void;
  /** Entity stopped matching (lost a component or was destroyed). */
  onRemoved?: (eid: number) => void;
}

export interface QueryWatcherHandle {
  /** Entities currently matching the query (snapshot copy). */
  current(): number[];
  /** Stop watching; callbacks never fire again. */
  dispose(): void;
}

interface Watcher {
  query: (world: State['world']) => number[];
  handlers: QueryWatcherHandlers;
  previous: Set<number>;
}

const watchersByState = new WeakMap<State, Set<Watcher>>();

function getWatchers(state: State): Set<Watcher> {
  let set = watchersByState.get(state);
  if (!set) {
    set = new Set();
    watchersByState.set(state, set);
  }
  return set;
}

/**
 * Flush all watchers once per frame. Runs in `late` so callbacks observe the
 * frame's final membership, deterministically, before draw.
 */
export const QueryWatcherSystem: System = defineSystem({
  name: 'QueryWatcherSystem',
  group: 'late',
  update: (state) => {
    const watchers = watchersByState.get(state);
    if (!watchers || watchers.size === 0) return;

    for (const watcher of watchers) {
      const current = watcher.query(state.world);
      const currentSet = new Set(current);

      const added: number[] = [];
      for (const eid of currentSet) {
        if (!watcher.previous.has(eid)) added.push(eid);
      }
      const removed: number[] = [];
      for (const eid of watcher.previous) {
        if (!currentSet.has(eid)) removed.push(eid);
      }
      watcher.previous = currentSet;

      dispatch(watcher, 'onAdded', added);
      dispatch(watcher, 'onRemoved', removed);
    }
  },
});

function dispatch(
  watcher: Watcher,
  key: 'onAdded' | 'onRemoved',
  eids: number[]
): void {
  if (eids.length === 0) return;
  const handler = watcher.handlers[key];
  if (!handler) return;
  for (const eid of eids) {
    try {
      handler(eid);
    } catch (err) {
      logger.error(
        `[VibeGame] watchQuery ${key} handler threw (eid=${eid}):`,
        err
      );
    }
  }
}

/**
 * React to query membership: `onAdded` fires for entities that start matching
 * the component set, `onRemoved` when they stop matching (including destroy).
 *
 * ```ts
 * const enemies = watchQuery(state, [Enemy, Health], {
 *   onAdded: (eid) => spawnHealthBar(eid),
 *   onRemoved: (eid) => despawnHealthBar(eid),
 * });
 * ```
 *
 * Callbacks are deferred to the end of the frame (deterministic ordering, no
 * re-entrancy inside systems). Registering the first watcher lazily registers
 * `QueryWatcherSystem`; no plugin setup is required.
 */
export function watchQuery(
  state: State,
  components: Record<string, unknown>[],
  handlers: QueryWatcherHandlers
): QueryWatcherHandle {
  const watcher: Watcher = {
    query: defineQuery(components),
    handlers,
    previous: new Set(),
  };

  const watchers = getWatchers(state);
  watchers.add(watcher);
  if (!state.systems.has(QueryWatcherSystem)) {
    state.registerSystem(QueryWatcherSystem);
  }

  return {
    current: () => [...watcher.query(state.world)],
    dispose: () => {
      watchers.delete(watcher);
    },
  };
}
