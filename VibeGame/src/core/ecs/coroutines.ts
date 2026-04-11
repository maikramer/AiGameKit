import type { State } from "./state";
import type { System } from "./types";
import type { CoroutineYieldValue } from "./yield-instructions";

export interface CoroutineEntry {
  generator: Generator;
  done: boolean;
  yieldValue?: CoroutineYieldValue;
  waitRemaining?: number;
}

type CoroutineMap = Map<number, CoroutineEntry>;

const coroutineState = new WeakMap<State, Map<number, CoroutineMap>>();
const nextIdState = new WeakMap<State, number>();

const endOfFrameQueue = new WeakMap<State, Array<{ eid: number; id: number }>>();
const fixedUpdateQueue = new WeakMap<State, Array<{ eid: number; id: number }>>();

function getOrCreateEntityMap(state: State, eid: number): CoroutineMap {
  let byState = coroutineState.get(state);
  if (!byState) {
    byState = new Map();
    coroutineState.set(state, byState);
  }
  let entityMap = byState.get(eid);
  if (!entityMap) {
    entityMap = new Map();
    byState.set(eid, entityMap);
  }
  return entityMap;
}

function allocId(state: State): number {
  let id = nextIdState.get(state) ?? 1;
  nextIdState.set(state, id + 1);
  return id;
}

const destroyRegistered = new WeakMap<State, Set<number>>();

function ensureDestroyCallback(state: State, eid: number): void {
  const registered = destroyRegistered.get(state);
  if (registered?.has(eid)) return;

  let set = destroyRegistered.get(state);
  if (!set) {
    set = new Set();
    destroyRegistered.set(state, set);
  }
  set.add(eid);

  state.onDestroy(eid, () => {
    cleanupEntityCoroutines(state, eid);
    set!.delete(eid);
  });
}

function getQueue<K>(map: WeakMap<State, Array<K>>, state: State): Array<K> {
  let queue = map.get(state);
  if (!queue) {
    queue = [];
    map.set(state, queue);
  }
  return queue;
}

function shouldAdvance(state: State, entry: CoroutineEntry): boolean {
  const yv = entry.yieldValue;
  if (yv === null || yv === undefined) return true;

  switch (yv.type) {
    case "waitForSeconds": {
      if (entry.waitRemaining === undefined) return true;
      entry.waitRemaining -= state.time.deltaTime;
      return entry.waitRemaining <= 0;
    }
    case "waitForSecondsRealtime": {
      if (entry.waitRemaining === undefined) return true;
      entry.waitRemaining -= state.time.unscaledDeltaTime;
      return entry.waitRemaining <= 0;
    }
    case "waitUntil":
      return yv.predicate();
    case "waitWhile":
      return !yv.predicate();
    case "waitForEndOfFrame":
    case "waitForFixedUpdate":
      return false;
    default:
      return true;
  }
}

function advanceGenerator(state: State, eid: number, id: number, entry: CoroutineEntry, entityMap: CoroutineMap): void {
  entry.yieldValue = undefined;
  entry.waitRemaining = undefined;

  const result = entry.generator.next();
  if (result.done) {
    entry.done = true;
    entityMap.delete(id);
    return;
  }

  const yielded: CoroutineYieldValue = result.value ?? null;
  entry.yieldValue = yielded;

  if (yielded !== null && yielded !== undefined) {
    if (yielded.type === "waitForEndOfFrame") {
      getQueue(endOfFrameQueue, state).push({ eid, id });
    } else if (yielded.type === "waitForFixedUpdate") {
      getQueue(fixedUpdateQueue, state).push({ eid, id });
    } else if (yielded.type === "waitForSeconds") {
      entry.waitRemaining = yielded.seconds;
    } else if (yielded.type === "waitForSecondsRealtime") {
      entry.waitRemaining = yielded.seconds;
    }
  }
}

export function startCoroutine(
  state: State,
  eid: number,
  genOrFn: Generator | (() => Generator),
): number {
  const gen = typeof genOrFn === "function" ? genOrFn() : genOrFn;
  const id = allocId(state);
  const entityMap = getOrCreateEntityMap(state, eid);
  const entry: CoroutineEntry = { generator: gen, done: false };
  entityMap.set(id, entry);
  ensureDestroyCallback(state, eid);

  const result = gen.next();
  if (result.done) {
    entry.done = true;
    entityMap.delete(id);
    if (entityMap.size === 0) {
      coroutineState.get(state)?.delete(eid);
    }
  } else {
    const yielded: CoroutineYieldValue = result.value ?? null;
    entry.yieldValue = yielded;
    if (yielded !== null && yielded !== undefined) {
      if (yielded.type === "waitForEndOfFrame") {
        getQueue(endOfFrameQueue, state).push({ eid, id });
      } else if (yielded.type === "waitForFixedUpdate") {
        getQueue(fixedUpdateQueue, state).push({ eid, id });
      } else if (yielded.type === "waitForSeconds") {
        entry.waitRemaining = yielded.seconds;
      } else if (yielded.type === "waitForSecondsRealtime") {
        entry.waitRemaining = yielded.seconds;
      }
    }
  }

  return id;
}

export function stopCoroutine(state: State, eid: number, coroutineId: number): void {
  coroutineState.get(state)?.get(eid)?.delete(coroutineId);
}

export function stopAllCoroutines(state: State, eid: number): void {
  coroutineState.get(state)?.get(eid)?.clear();
  coroutineState.get(state)?.delete(eid);
}

export function getActiveCoroutines(state: State, eid: number): CoroutineMap | undefined {
  return coroutineState.get(state)?.get(eid);
}

export function getCoroutine(state: State, eid: number, id: number): CoroutineEntry | undefined {
  return coroutineState.get(state)?.get(eid)?.get(id);
}

export function cleanupEntityCoroutines(state: State, eid: number): void {
  const byState = coroutineState.get(state);
  if (!byState) return;
  byState.delete(eid);
}

export const CoroutineRunnerSystem: System = {
  group: "simulation",
  update(state: State): void {
    const byState = coroutineState.get(state);
    if (!byState) return;

    for (const [eid, entityMap] of byState) {
      if (!state.exists(eid)) {
        byState.delete(eid);
        continue;
      }

      for (const [id, entry] of entityMap) {
        if (entry.done) continue;
        if (!shouldAdvance(state, entry)) continue;
        advanceGenerator(state, eid, id, entry, entityMap);
      }

      if (entityMap.size === 0) {
        byState.delete(eid);
      }
    }
  },
};

function processQueue(
  state: State,
  queueMap: WeakMap<State, Array<{ eid: number; id: number }>>,
): void {
  const queue = queueMap.get(state);
  if (!queue || queue.length === 0) return;

  const current = queue.splice(0, queue.length);
  const byState = coroutineState.get(state);
  if (!byState) return;

  for (const { eid, id } of current) {
    const entityMap = byState.get(eid);
    if (!entityMap) continue;
    const entry = entityMap.get(id);
    if (!entry || entry.done) continue;

    advanceGenerator(state, eid, id, entry, entityMap);

    if (entityMap.size === 0) {
      byState.delete(eid);
    }
  }
}

export const CoroutineLateFrameSystem: System = {
  group: "late",
  update(state: State): void {
    processQueue(state, endOfFrameQueue);
  },
};

export const CoroutineFixedUpdateSystem: System = {
  group: "fixed",
  update(state: State): void {
    processQueue(state, fixedUpdateQueue);
  },
};
