type Callback = (data?: unknown) => void;

const entityListeners = new Map<number, Map<string, Set<Callback>>>();

function getOrCreateListenerMap(eid: number): Map<string, Set<Callback>> {
  let map = entityListeners.get(eid);
  if (!map) {
    map = new Map();
    entityListeners.set(eid, map);
  }
  return map;
}

export function addEventListener(
  eid: number,
  eventName: string,
  callback: Callback
): void {
  const map = getOrCreateListenerMap(eid);
  let set = map.get(eventName);
  if (!set) {
    set = new Set();
    map.set(eventName, set);
  }
  set.add(callback);
}

export function removeEventListener(
  eid: number,
  eventName: string,
  callback: Callback
): void {
  entityListeners.get(eid)?.get(eventName)?.delete(callback);
}

export function addEventListenerOnce(
  eid: number,
  eventName: string,
  callback: Callback
): void {
  const wrapper: Callback = (data) => {
    removeEventListener(eid, eventName, wrapper);
    callback(data);
  };
  addEventListener(eid, eventName, wrapper);
}

export function dispatchEvent(
  eid: number,
  eventName: string,
  data?: unknown
): void {
  const map = entityListeners.get(eid);
  const set = map?.get(eventName);
  if (!set) return;
  for (const cb of set) {
    cb(data);
  }
}

export function removeAllListeners(eid: number, eventName?: string): void {
  const map = entityListeners.get(eid);
  if (!map) return;
  if (eventName !== undefined) {
    map.delete(eventName);
  } else {
    map.clear();
    entityListeners.delete(eid);
  }
}
