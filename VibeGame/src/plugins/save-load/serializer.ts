import { commitRemovals } from 'bitecs';
import { Packr } from 'msgpackr';
import {
  createSnapshot,
  defineQuery,
  restoreSnapshot,
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

export function loadSnapshot(
  state: State,
  data: Uint8Array,
  options: { clearExisting?: boolean } = {}
): void {
  const payload = packr.unpack(data) as WorldSnapshot & {
    serializableEids?: number[];
  };

  if (options.clearExisting) {
    const existing = serializableQuery(state.world);
    for (const eid of existing) {
      if (state.exists(eid)) state.destroyEntity(eid);
    }
    commitRemovals(state.world);
  }

  restoreSnapshot(state, payload);

  if (
    typeof window !== 'undefined' &&
    window.dispatchEvent &&
    typeof window.CustomEvent === 'function'
  ) {
    window.dispatchEvent(
      new window.CustomEvent('snapshot-loaded', { detail: payload })
    );
  }
}

// ── localStorage persistence. The full world snapshot is far too big for the
//    ~5 MB localStorage quota as a JSON number array, so saves are gzip'd with
//    the native CompressionStream ('vg1:' prefix). Legacy JSON-array saves
//    (pre-compression) are still read back. ──────────────────────────────────
const SAVE_PREFIX = 'vg1:';

function toBase64(bytes: Uint8Array): string {
  let bin = '';
  for (let i = 0; i < bytes.length; i += 0x8000) {
    bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(bin);
}

function fromBase64(b64: string): Uint8Array {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

async function compress(data: Uint8Array): Promise<string> {
  if (typeof CompressionStream === 'undefined') {
    return JSON.stringify(Array.from(data)); // legacy path (no gzip support)
  }
  // Uint8Array.from allocates a plain ArrayBuffer (BlobPart requires it).
  const stream = new Blob([Uint8Array.from(data)])
    .stream()
    .pipeThrough(new CompressionStream('gzip'));
  const buf = new Uint8Array(await new Response(stream).arrayBuffer());
  return SAVE_PREFIX + toBase64(buf);
}

async function decompress(encoded: string): Promise<Uint8Array | null> {
  if (encoded.startsWith(SAVE_PREFIX)) {
    if (typeof DecompressionStream === 'undefined') return null;
    const stream = new Blob([
      Uint8Array.from(fromBase64(encoded.slice(SAVE_PREFIX.length))),
    ])
      .stream()
      .pipeThrough(new DecompressionStream('gzip'));
    return new Uint8Array(await new Response(stream).arrayBuffer());
  }
  return Uint8Array.from(JSON.parse(encoded) as number[]);
}

export async function saveToLocalStorage(
  state: State,
  key: string
): Promise<void> {
  if (typeof localStorage === 'undefined') return;
  const buf = saveSnapshot(state);
  localStorage.setItem(key, await compress(buf));
}

export async function loadFromLocalStorage(
  state: State,
  key: string
): Promise<boolean> {
  if (typeof localStorage === 'undefined') return false;
  const raw = localStorage.getItem(key);
  if (!raw) return false;
  const data = await decompress(raw);
  if (!data) return false;
  loadSnapshot(state, data);
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
