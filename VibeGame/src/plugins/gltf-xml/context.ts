import type { State } from '../../core';

const urlByState = new WeakMap<State, Map<number, string>>();
const inFlightByState = new WeakMap<State, Set<number>>();
const lodUrlsByState = new WeakMap<
  State,
  Map<number, readonly [string, string, string]>
>();
const pendingLodThresholdsByState = new WeakMap<
  State,
  Map<number, { near?: number; mid?: number }>
>();

export function setGltfUrl(state: State, entity: number, url: string): void {
  let m = urlByState.get(state);
  if (!m) {
    m = new Map();
    urlByState.set(state, m);
  }
  m.set(entity, url.trim());
}

export function getGltfUrl(state: State, entity: number): string | undefined {
  return urlByState.get(state)?.get(entity);
}

export function isGltfInFlight(state: State, entity: number): boolean {
  return inFlightByState.get(state)?.has(entity) ?? false;
}

export function setGltfInFlight(
  state: State,
  entity: number,
  v: boolean
): void {
  let s = inFlightByState.get(state);
  if (!s) {
    s = new Set();
    inFlightByState.set(state, s);
  }
  if (v) {
    s.add(entity);
  } else {
    s.delete(entity);
  }
}

export function setGltfLodUrls(
  state: State,
  entity: number,
  urls: readonly [string, string, string]
): void {
  let m = lodUrlsByState.get(state);
  if (!m) {
    m = new Map();
    lodUrlsByState.set(state, m);
  }
  m.set(entity, [urls[0].trim(), urls[1].trim(), urls[2].trim()]);
}

export function getGltfLodUrls(
  state: State,
  entity: number
): readonly [string, string, string] | undefined {
  return lodUrlsByState.get(state)?.get(entity);
}

export function clearGltfLodUrls(state: State, entity: number): void {
  lodUrlsByState.get(state)?.delete(entity);
  pendingLodThresholdsByState.get(state)?.delete(entity);
}

export function setPendingLodThresholdNear(
  state: State,
  entity: number,
  value: number
): void {
  let m = pendingLodThresholdsByState.get(state);
  if (!m) {
    m = new Map();
    pendingLodThresholdsByState.set(state, m);
  }
  const cur = m.get(entity) ?? {};
  cur.near = value;
  m.set(entity, cur);
}

export function setPendingLodThresholdMid(
  state: State,
  entity: number,
  value: number
): void {
  let m = pendingLodThresholdsByState.get(state);
  if (!m) {
    m = new Map();
    pendingLodThresholdsByState.set(state, m);
  }
  const cur = m.get(entity) ?? {};
  cur.mid = value;
  m.set(entity, cur);
}

export function applyPendingLodThresholds(
  state: State,
  entity: number,
  setNear: (v: number) => void,
  setMid: (v: number) => void
): void {
  const p = pendingLodThresholdsByState.get(state)?.get(entity);
  if (!p) return;
  if (p.near !== undefined) setNear(p.near);
  if (p.mid !== undefined) setMid(p.mid);
  pendingLodThresholdsByState.get(state)?.delete(entity);
}
