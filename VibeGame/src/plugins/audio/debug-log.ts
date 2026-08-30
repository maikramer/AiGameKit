/**
 * Ring-buffer of audio play/stop events for the in-game profiler Audio tab.
 * Always records lightweight events from the first bank/system call (loading
 * included). Stack traces are captured only when debug is armed (URL audio tab
 * or Audio tab focused).
 */
import { Howler } from 'howler';

export type AudioDebugEventKind =
  | 'play'
  | 'stop'
  | 'end'
  | 'unknown'
  | 'fade'
  | 'busMute'
  | 'skip'
  | 'preload'
  | 'queue';

export type AudioDebugSource = 'bank' | 'audio-source' | 'emitter' | 'music';

export interface AudioDebugEvent {
  t: number;
  kind: AudioDebugEventKind;
  key: string;
  source: AudioDebugSource;
  bus?: string;
  volume?: number;
  loop?: boolean;
  spatial?: boolean;
  followEid?: number;
  /** Entity that caused the play (may differ from followEid). */
  originEid?: number;
  /** Resolved ``state.getEntityName(originEid)`` when available. */
  originName?: string;
  /**
   * Stable human label for the profiler, e.g. ``goblin#42``, ``boot/preload``,
   * ``ui``, ``music``, ``world``.
   */
  origin?: string;
  pos?: [number, number, number];
  howlId?: number;
  ctxState?: string;
  caller?: string;
  detail?: string;
}

export interface AudioDebugActivePlay {
  key: string;
  bus: string;
  volume: number;
  loop: boolean;
  spatial: boolean;
  followEid?: number;
  originEid?: number;
  originName?: string;
  origin?: string;
  howlId: number;
  ageMs: number;
  startedAt: number;
}

export interface AudioDebugSnapshot {
  armed: boolean;
  ctxState: string;
  masterVolume: number;
  buses: { name: string; volume: number; muted: boolean }[];
  active: AudioDebugActivePlay[];
  events: AudioDebugEvent[];
  playsLastSec: number;
  topKeys: { key: string; count: number }[];
  /** Play counts grouped by ``origin`` label (gameplay only). */
  topOrigins: { origin: string; count: number }[];
  /** Silent boot cache warms (``kind=preload``) — not audible plays. */
  preloadCount: number;
  unknownKeys: string[];
}

const RING_SIZE = 256;
const STACK_FRAMES = 6;

const ring: AudioDebugEvent[] = [];
let writeIndex = 0;
let count = 0;
let armed = false;

/** Optional provider for live bank state (wired by bank.ts to avoid cycles). */
let activeProvider: (() => AudioDebugActivePlay[]) | null = null;
let busProvider:
  (() => { name: string; volume: number; muted: boolean }[]) | null = null;
let masterProvider: (() => number) | null = null;
/** Resolve entity display names for origin labels (wired by AudioSystem). */
let entityNameProvider: ((eid: number) => string | undefined) | null = null;

export function setAudioDebugProviders(opts: {
  listActive: () => AudioDebugActivePlay[];
  listBuses: () => { name: string; volume: number; muted: boolean }[];
  getMaster: () => number;
}): void {
  activeProvider = opts.listActive;
  busProvider = opts.listBuses;
  masterProvider = opts.getMaster;
}

export function setAudioEntityNameProvider(
  fn: ((eid: number) => string | undefined) | null
): void {
  entityNameProvider = fn;
}

/** Build ``goblin#42`` / ``eid#42`` / fallback tag for the profiler. */
export function formatAudioOrigin(opts: {
  originEid?: number;
  originName?: string;
  origin?: string;
  followEid?: number;
  fallback?: string;
}): {
  originEid?: number;
  originName?: string;
  origin: string;
} {
  const originEid = opts.originEid ?? opts.followEid;
  const originName =
    opts.originName ??
    (originEid != null ? entityNameProvider?.(originEid) : undefined);
  if (opts.origin) {
    return { originEid, originName, origin: opts.origin };
  }
  if (originEid != null) {
    return {
      originEid,
      originName,
      origin: originName ? `${originName}#${originEid}` : `eid#${originEid}`,
    };
  }
  return { origin: opts.fallback ?? 'none' };
}

export function armAudioDebug(on: boolean): void {
  armed = on;
}

export function isAudioDebugArmed(): boolean {
  return armed;
}

/** Arm from URL as early as possible (AudioPlugin / ProfilerPlugin init). */
export function armAudioDebugFromUrl(
  search = typeof window !== 'undefined' ? window.location.search : ''
): boolean {
  try {
    const params = new URLSearchParams(search);
    const profiler = (params.get('profiler') ?? '').trim().toLowerCase();
    const tab = (params.get('profilerTab') ?? '').trim().toLowerCase();
    const want =
      profiler === 'audio' ||
      tab === 'audio' ||
      params.get('audioDebug') === '1';
    if (want) armAudioDebug(true);
    return want;
  } catch {
    return false;
  }
}

function nowMs(): number {
  return typeof performance !== 'undefined' ? performance.now() : Date.now();
}

function ctxState(): string {
  try {
    return Howler.ctx?.state ?? 'none';
  } catch {
    return 'none';
  }
}

function captureCaller(): string | undefined {
  if (!armed) return undefined;
  try {
    const stack = new Error().stack;
    if (!stack) return '(no-stack)';
    const lines = stack.split('\n').slice(1);
    const useful: string[] = [];
    for (const raw of lines) {
      const line = raw.trim().replace(/^at\s+/, '');
      if (!line) continue;
      if (line.includes('captureCaller')) continue;
      if (line.includes('recordAudioDebugEvent')) continue;
      if (line.includes('formatAudioOrigin')) continue;
      if (line.includes('/debug-log.')) continue;
      // Skip bank internals so game scripts / entity scripts surface first.
      if (line.includes('/plugins/audio/bank.')) continue;
      if (line.includes('playInternal')) continue;
      useful.push(line);
      if (useful.length >= STACK_FRAMES) break;
    }
    return useful.length > 0 ? useful.join(' ← ') : '(no-frames)';
  } catch {
    return '(stack-error)';
  }
}

export function recordAudioDebugEvent(
  partial: Omit<AudioDebugEvent, 't' | 'ctxState' | 'caller'> & {
    caller?: string;
    ctxState?: string;
  }
): void {
  const ev: AudioDebugEvent = {
    t: nowMs(),
    ctxState: partial.ctxState ?? ctxState(),
    caller: partial.caller ?? captureCaller(),
    ...partial,
  };
  if (ring.length < RING_SIZE) {
    ring.push(ev);
    count = ring.length;
    writeIndex = count % RING_SIZE;
  } else {
    ring[writeIndex] = ev;
    writeIndex = (writeIndex + 1) % RING_SIZE;
    count = RING_SIZE;
  }
}

export function clearAudioDebugLog(opts?: { keepPreload?: boolean }): void {
  if (opts?.keepPreload) {
    const kept = getAudioDebugEvents().filter((e) => e.kind === 'preload');
    ring.length = 0;
    writeIndex = 0;
    count = 0;
    for (const e of kept) {
      ring.push(e);
    }
    count = ring.length;
    writeIndex = count % RING_SIZE;
    return;
  }
  ring.length = 0;
  writeIndex = 0;
  count = 0;
}

export function getAudioDebugEvents(): AudioDebugEvent[] {
  if (count < RING_SIZE) return ring.slice();
  return [...ring.slice(writeIndex), ...ring.slice(0, writeIndex)];
}

export function getAudioDebugSnapshot(): AudioDebugSnapshot {
  const events = getAudioDebugEvents();
  const tNow = nowMs();
  const recent = events.filter((e) => tNow - e.t <= 1000 && e.kind === 'play');
  const keyCounts = new Map<string, number>();
  const originCounts = new Map<string, number>();
  const unknowns = new Set<string>();
  let preloadCount = 0;
  for (const e of events) {
    if (e.kind === 'unknown') unknowns.add(e.key);
    if (e.kind === 'preload') preloadCount += 1;
    if (e.kind === 'play') {
      keyCounts.set(e.key, (keyCounts.get(e.key) ?? 0) + 1);
      const o = e.origin ?? 'none';
      originCounts.set(o, (originCounts.get(o) ?? 0) + 1);
    }
  }
  const topKeys = [...keyCounts.entries()]
    .map(([key, c]) => ({ key, count: c }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 12);
  const topOrigins = [...originCounts.entries()]
    .map(([origin, c]) => ({ origin, count: c }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 12);

  return {
    armed,
    ctxState: ctxState(),
    masterVolume: masterProvider?.() ?? 1,
    buses: busProvider?.() ?? [],
    active: activeProvider?.() ?? [],
    events,
    playsLastSec: recent.length,
    topKeys,
    topOrigins,
    preloadCount,
    unknownKeys: [...unknowns],
  };
}

/** Test helper — wipe providers + ring + arm. */
export function _resetAudioDebugLog(): void {
  clearAudioDebugLog();
  armed = false;
  activeProvider = null;
  busProvider = null;
  masterProvider = null;
  entityNameProvider = null;
}
