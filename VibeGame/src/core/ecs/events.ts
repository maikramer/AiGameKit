import type { State } from './state';
import { logger } from '../utils/logger';

type Callback = (data?: unknown) => void;

const entityListenersByState = new WeakMap<
  State,
  Map<number, Map<string, Set<Callback>>>
>();

function getListeners(state: State): Map<number, Map<string, Set<Callback>>> {
  let map = entityListenersByState.get(state);
  if (!map) {
    map = new Map();
    entityListenersByState.set(state, map);
  }
  return map;
}

function getOrCreateListenerMap(
  state: State,
  eid: number
): Map<string, Set<Callback>> {
  const listeners = getListeners(state);
  let map = listeners.get(eid);
  if (!map) {
    map = new Map();
    listeners.set(eid, map);
  }
  return map;
}

export function addEventListener(
  state: State,
  eid: number,
  eventName: string,
  callback: Callback
): void {
  const map = getOrCreateListenerMap(state, eid);
  let set = map.get(eventName);
  if (!set) {
    set = new Set();
    map.set(eventName, set);
  }
  set.add(callback);
}

export function removeEventListener(
  state: State,
  eid: number,
  eventName: string,
  callback: Callback
): void {
  getListeners(state).get(eid)?.get(eventName)?.delete(callback);
}

export function addEventListenerOnce(
  state: State,
  eid: number,
  eventName: string,
  callback: Callback
): void {
  const wrapper: Callback = (data) => {
    removeEventListener(state, eid, eventName, wrapper);
    callback(data);
  };
  addEventListener(state, eid, eventName, wrapper);
}

export function dispatchEvent(
  state: State,
  eid: number,
  eventName: string,
  data?: unknown
): void {
  const map = getListeners(state).get(eid);
  const set = map?.get(eventName);
  if (!set) return;
  for (const cb of set) {
    try {
      cb(data);
    } catch (err) {
      logger.error(
        `[VibeGame] dispatchEvent listener threw (eid=${eid}, event="${eventName}"):`,
        err
      );
    }
  }
}

export function removeAllListeners(
  state: State,
  eid: number,
  eventName?: string
): void {
  const listeners = getListeners(state);
  const map = listeners.get(eid);
  if (!map) return;
  if (eventName !== undefined) {
    map.delete(eventName);
  } else {
    map.clear();
    listeners.delete(eid);
  }
}
