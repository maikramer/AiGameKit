/* eslint-disable no-undef -- CustomEvent e localStorage só no browser */
import { Packr } from 'msgpackr';
import {
  createSnapshot,
  defineQuery,
  type State,
  type WorldSnapshot,
} from '../../core';
import { Serializable } from './components';

const packr = new Packr();

const serializableQuery = defineQuery([Serializable]);

let nextSerializationId = 1;

export function saveSnapshot(state: State): Uint8Array {
  const snap = createSnapshot(state);
  const payload: WorldSnapshot & { serializableEids?: number[] } = { ...snap };
  const eids: number[] = [];
  for (const eid of serializableQuery(state.world)) {
    if (Serializable.flag[eid]) eids.push(eid);
  }
  payload.serializableEids = eids;
  return packr.pack(payload) as Uint8Array;
}

export function loadSnapshot(state: State, data: Uint8Array): void {
  const payload = packr.unpack(data) as WorldSnapshot & {
    serializableEids?: number[];
  };
  state.time.elapsed = payload.elapsed ?? 0;
  if (
    typeof window !== 'undefined' &&
    window.dispatchEvent &&
    typeof window.CustomEvent === 'function'
  ) {
    window.dispatchEvent(
      new window.CustomEvent('snapshot-loaded', { detail: payload })
    );
  }
  void payload;
  void state;
}

export function saveToLocalStorage(state: State, key: string): void {
  if (typeof localStorage === 'undefined') return;
  const buf = saveSnapshot(state);
  localStorage.setItem(key, JSON.stringify(Array.from(buf)));
}

export function loadFromLocalStorage(state: State, key: string): boolean {
  if (typeof localStorage === 'undefined') return false;
  const raw = localStorage.getItem(key);
  if (!raw) return false;
  const arr = JSON.parse(raw) as number[];
  const bin = new Uint8Array(arr);
  loadSnapshot(state, bin);
  return true;
}

export function assignSerializationIds(state: State): void {
  for (const eid of serializableQuery(state.world)) {
    if (!Serializable.flag[eid]) continue;
    if (Serializable.serializationId[eid] === 0) {
      Serializable.serializationId[eid] = nextSerializationId++;
    }
  }
}
