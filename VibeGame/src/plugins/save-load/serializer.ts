import { commitRemovals } from 'bitecs';
import { Packr } from 'msgpackr';
import {
  createSnapshot,
  defineQuery,
  restoreSnapshot,
  type State,
  type WorldSnapshot,
} from '../../core';
import { logger } from '../../core/utils/logger';
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

type SnapshotPayload = WorldSnapshot & { serializableEids?: number[] };

function isSnapshotPayload(data: unknown): data is SnapshotPayload {
  if (!data || typeof data !== 'object') return false;
  const snap = data as Partial<WorldSnapshot>;
  if (typeof snap.elapsed !== 'number' || !Number.isFinite(snap.elapsed)) {
    return false;
  }
  if (!Array.isArray(snap.entities)) return false;
  for (const ent of snap.entities) {
    if (!ent || typeof ent !== 'object') return false;
    if (
      typeof ent.eid !== 'number' ||
      !Number.isInteger(ent.eid) ||
      ent.eid < 0 ||
      !ent.components ||
      typeof ent.components !== 'object'
    ) {
      return false;
    }
  }
  return true;
}

export function loadSnapshot(
  state: State,
  data: Uint8Array,
  options: { clearExisting?: boolean } = {}
): void {
  let payload: SnapshotPayload;
  try {
    payload = packr.unpack(data) as SnapshotPayload;
  } catch (err) {
    throw new Error(
      `Save data is not a valid world snapshot: ${err instanceof Error ? err.message : err}`,
      { cause: err }
    );
  }
  // Validate BEFORE destroying the existing world — a corrupt save must leave
  // the live session untouched, not brick it half-cleared.
  if (!isSnapshotPayload(payload)) {
    throw new Error(
      'Save data is not a valid world snapshot (elapsed/entities)'
    );
  }

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
  try {
    if (encoded.startsWith(SAVE_PREFIX)) {
      if (typeof DecompressionStream === 'undefined') return null;
      const stream = new Blob([
        Uint8Array.from(fromBase64(encoded.slice(SAVE_PREFIX.length))),
      ])
        .stream()
        .pipeThrough(new DecompressionStream('gzip'));
      return new Uint8Array(await new Response(stream).arrayBuffer());
    }
    // Legacy JSON-array saves: reject anything that isn't a byte array —
    // a foreign value under this key must never reach the msgpack unpacker.
    const parsed: unknown = JSON.parse(encoded);
    if (
      !Array.isArray(parsed) ||
      parsed.length === 0 ||
      !parsed.every(
        (v) =>
          typeof v === 'number' && Number.isInteger(v) && v >= 0 && v <= 255
      )
    ) {
      return null;
    }
    return Uint8Array.from(parsed);
  } catch {
    return null;
  }
}

export async function saveToLocalStorage(
  state: State,
  key: string
): Promise<boolean> {
  if (typeof localStorage === 'undefined') return false;
  let encoded: string;
  try {
    encoded = await compress(saveSnapshot(state));
  } catch (err) {
    logger.error(
      `[save-load] falha ao serializar save "${key}": ${err instanceof Error ? err.message : err}`
    );
    return false;
  }
  try {
    localStorage.setItem(key, encoded);
    return true;
  } catch {
    // Quota exceeded: the previous save under this key is superseded anyway —
    // evict it and retry once before giving up.
    try {
      localStorage.removeItem(key);
      localStorage.setItem(key, encoded);
      return true;
    } catch (err) {
      logger.error(
        `[save-load] quota de localStorage ao gravar "${key}": ${err instanceof Error ? err.message : err} — save descartado`
      );
      return false;
    }
  }
}

export async function loadFromLocalStorage(
  state: State,
  key: string
): Promise<boolean> {
  if (typeof localStorage === 'undefined') return false;
  const raw = localStorage.getItem(key);
  if (!raw) return false;
  const data = await decompress(raw);
  if (!data) {
    logger.warn(`[save-load] save corrupto em "${key}" — ignorado`);
    return false;
  }
  try {
    loadSnapshot(state, data);
  } catch (err) {
    logger.warn(
      `[save-load] save ilegível em "${key}" (${err instanceof Error ? err.message : err}) — ignorado`
    );
    return false;
  }
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
