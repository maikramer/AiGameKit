import { clamp01 } from '../../shared';
import { logger } from '../../core/utils/logger';
/**
 * Sound bank: declare sounds once by key, then fire them from anywhere with no
 * entity, no eid lookup, and no per-frame plumbing.
 *
 *   defineSoundBank({
 *     coin: { url: '/assets/audio/coin.ogg', volume: 0.5, bus: 'sfx' },
 *     bgm:  { url: '/assets/audio/bgm.wav', loop: true, bus: 'music', volume: 0.2 },
 *   });
 *
 *   playSound('coin');                 // 2D one-shot (overlaps freely)
 *   playSoundAt('boom', x, y, z);      // spatial one-shot
 *   playSoundOn(eid, 'footstep');      // spatial, follows the entity
 *   const h = playSound('bgm'); h.fadeOut(1);
 *
 * Volume is routed through named buses (master × bus × clip), so a game can
 * expose a single "SFX volume" / "Music volume" slider without touching emitters.
 */
import { Howl } from 'howler';
import {
  formatAudioOrigin,
  recordAudioDebugEvent,
  setAudioDebugProviders,
  type AudioDebugActivePlay,
} from './debug-log';

export interface SoundDef {
  /** Asset URL (one entry per key). */
  url: string;
  /** Base clip gain 0..1 (before bus/master). Default 1. */
  volume?: number;
  /** Bus this sound routes through (e.g. 'sfx', 'music', 'ui'). Default 'sfx'. */
  bus?: string;
  /** Loop forever (background music, ambience). Default false. */
  loop?: boolean;
  /** Playback rate / pitch. Default 1. */
  pitch?: number;
  /** Positional audio (stereo panning via Howler). Default false. */
  spatial?: boolean;
  /** Spatial: distance at which attenuation begins. Default 1. */
  minDistance?: number;
  /** Spatial: distance at which the sound is silent. Default 100. */
  maxDistance?: number;
  /** Spatial: rolloff factor. Default 1. */
  rolloff?: number;
}

export interface PlayOptions {
  /** Override the clip's base volume for this play (0..1). */
  volume?: number;
  /** Override pitch for this play. */
  pitch?: number;
  /** Override the routing bus for this play. */
  bus?: string;
  /**
   * Entity that caused this play (profiler ``origin=name#eid``).
   * Prefer this on ``playSoundAt`` so far/world SFX show who fired them.
   */
  originEid?: number;
  /**
   * Explicit origin tag when there is no entity
   * (e.g. ``ui``, ``music``, ``boot/preload``).
   */
  origin?: string;
}

export interface SoundHandle {
  readonly key: string;
  readonly id: number;
  stop(): void;
  setVolume(v: number): void;
  fadeOut(seconds: number): void;
  fadeIn(toVolume: number, seconds: number): void;
  setPosition(x: number, y: number, z: number): void;
}

interface BusState {
  volume: number;
  muted: boolean;
}

interface ActivePlay {
  key: string;
  howl: Howl;
  id: number;
  baseVolume: number;
  busName: string;
  loop: boolean;
  spatial: boolean;
  startedAt: number;
  /** When set, FollowEmitterSystem repositions this play to the entity each frame. */
  followEid?: number;
  originEid?: number;
  originName?: string;
  origin?: string;
  /** Last spatial pos pushed to Howler (skip setPos when unchanged). */
  lastX?: number;
  lastY?: number;
  lastZ?: number;
}

const bank = new Map<string, SoundDef>();
/** Separate caches: a 2D preload must not poison later spatial plays. */
const howls = new Map<string, Howl>();
const howlsSpatial = new Map<string, Howl>();
const buses = new Map<string, BusState>();
const active = new Set<ActivePlay>();

let masterVolume = 1;
// Disabled under headless (node/tests) where there is no Web Audio context.
let audioEnabled = typeof window !== 'undefined';

/**
 * Browsers warn if Howler creates an AudioContext before a user gesture.
 * Preloads queue until {@link allowSoundPreload} (wired to first pointerdown).
 * Headless / no-DOM environments unlock immediately.
 */
let soundPreloadAllowed =
  typeof document === 'undefined' ||
  typeof document.addEventListener !== 'function';
const pendingSoundPreload = new Set<string>();

/** Last listener world pose (written by AudioSystem). Used to cull far SFX. */
let listenerX = 0;
let listenerY = 0;
let listenerZ = 0;
let listenerValid = false;

/** Push the audio listener pose so spatial plays can cull beyond maxDistance. */
export function setAudioListenerWorldPos(
  x: number,
  y: number,
  z: number
): void {
  listenerX = x;
  listenerY = y;
  listenerZ = z;
  listenerValid = true;
}

export function getAudioListenerWorldPos(): {
  x: number;
  y: number;
  z: number;
} | null {
  return listenerValid ? { x: listenerX, y: listenerY, z: listenerZ } : null;
}

function isBeyondHearRange(
  x: number,
  y: number,
  z: number,
  maxDistance: number
): boolean {
  if (!listenerValid || !(maxDistance > 0)) return false;
  const dx = x - listenerX;
  const dy = y - listenerY;
  const dz = z - listenerZ;
  return dx * dx + dy * dy + dz * dz > maxDistance * maxDistance;
}

function bus(name: string): BusState {
  let b = buses.get(name);
  if (!b) {
    b = { volume: 1, muted: false };
    buses.set(name, b);
  }
  return b;
}

function busGain(name: string): number {
  const b = bus(name);
  return b.muted ? 0 : masterVolume * b.volume;
}

function gainFor(ap: ActivePlay): number {
  return ap.baseVolume * busGain(ap.busName);
}

/** Re-apply gain to every active play (call after a bus/master change). */
function applyAllGains(): void {
  for (const ap of active) {
    ap.howl.volume(gainFor(ap), ap.id);
  }
}

// ── Declaration ──────────────────────────────────────────────────────────────

/** Register sounds by key. Call as many times as you like; later keys win. */
export function defineSoundBank(defs: Record<string, SoundDef>): void {
  for (const [key, def] of Object.entries(defs)) {
    bank.set(key, def);
    // A redefined key should rebuild its Howl on next play.
    for (const map of [howls, howlsSpatial]) {
      const existing = map.get(key);
      if (existing) {
        existing.unload();
        map.delete(key);
      }
    }
  }
}

/** Read a registered sound definition (used by the `sound=` XML adapter). */
export function getSoundDef(key: string): SoundDef | undefined {
  return bank.get(key);
}

// ── Buses ────────────────────────────────────────────────────────────────────

export function setMasterVolume(v: number): void {
  masterVolume = clamp01(v);
  applyAllGains();
}

export function getMasterVolume(): number {
  return masterVolume;
}

export function setBusVolume(name: string, v: number): void {
  bus(name).volume = clamp01(v);
  applyAllGains();
}

export function getBusVolume(name: string): number {
  return bus(name).volume;
}

export function setBusMuted(name: string, muted: boolean): void {
  bus(name).muted = muted;
  applyAllGains();
  recordAudioDebugEvent({
    kind: 'busMute',
    key: name,
    source: 'bank',
    bus: name,
    detail: muted ? 'muted' : 'unmuted',
  });
}

export function isBusMuted(name: string): boolean {
  return bus(name).muted;
}

/** Enable/disable all bank playback (engine forces off under headless). */
export function setAudioEnabled(enabled: boolean): void {
  audioEnabled = enabled;
}

// ── Playback ─────────────────────────────────────────────────────────────────

function ensureHowl(
  key: string,
  def: SoundDef,
  wantSpatial: boolean
): Howl | null {
  if (!audioEnabled) return null;
  const map = wantSpatial ? howlsSpatial : howls;
  let h = map.get(key);
  if (!h) {
    h = new Howl({
      src: [def.url],
      preload: true,
      loop: def.loop ?? false,
      volume: def.volume ?? 1,
      rate: def.pitch ?? 1,
      ...(wantSpatial && {
        pannerAttr: {
          refDistance: def.minDistance ?? 1,
          maxDistance: def.maxDistance ?? 40,
          rolloffFactor: def.rolloff ?? 1,
        },
      }),
    });
    map.set(key, h);
  }
  return h;
}

const NULL_HANDLE: SoundHandle = {
  key: '',
  id: -1,
  stop() {},
  setVolume() {},
  fadeOut() {},
  fadeIn() {},
  setPosition() {},
};

function makeHandle(ap: ActivePlay): SoundHandle {
  return {
    key: ap.key,
    id: ap.id,
    stop() {
      ap.howl.stop(ap.id);
      active.delete(ap);
      recordAudioDebugEvent({
        kind: 'stop',
        key: ap.key,
        source: 'bank',
        bus: ap.busName,
        howlId: ap.id,
        followEid: ap.followEid,
      });
    },
    setVolume(v: number) {
      ap.baseVolume = clamp01(v);
      ap.howl.volume(gainFor(ap), ap.id);
    },
    fadeOut(seconds: number) {
      const from = gainFor(ap);
      ap.howl.fade(from, 0, Math.max(0, seconds) * 1000, ap.id);
      ap.howl.once('fade', () => ap.howl.stop(ap.id), ap.id);
      active.delete(ap);
      recordAudioDebugEvent({
        kind: 'fade',
        key: ap.key,
        source: 'bank',
        bus: ap.busName,
        howlId: ap.id,
        detail: `fadeOut ${seconds.toFixed(2)}s`,
      });
    },
    fadeIn(toVolume: number, seconds: number) {
      ap.baseVolume = clamp01(toVolume);
      ap.howl.fade(0, gainFor(ap), Math.max(0, seconds) * 1000, ap.id);
      recordAudioDebugEvent({
        kind: 'fade',
        key: ap.key,
        source: 'bank',
        bus: ap.busName,
        howlId: ap.id,
        volume: toVolume,
        detail: `fadeIn ${seconds.toFixed(2)}s`,
      });
    },
    setPosition(x: number, y: number, z: number) {
      ap.howl.pos(x, y, z, ap.id);
    },
  };
}

function playInternal(
  key: string,
  opts: PlayOptions | undefined,
  followEid: number | undefined,
  pos: [number, number, number] | undefined
): SoundHandle {
  const def = bank.get(key);
  if (!def) {
    logger.warn(`[audio] playSound: unknown key "${key}"`);
    recordAudioDebugEvent({
      kind: 'unknown',
      key,
      source: 'bank',
    });
    return NULL_HANDLE;
  }
  const busName = opts?.bus ?? def.bus ?? 'sfx';
  const loop = def.loop ?? false;
  // Spatial only when we have a world anchor. ``def.spatial`` marks clips that
  // *should* be played via playSoundAt/On — bare playSound stays 2D (UI/preload)
  // so they never spawn at (0,0,0) and haunt the map.
  const spatial = !!(pos || followEid !== undefined);
  const maxDistance = def.maxDistance ?? 40;
  const originInfo = formatAudioOrigin({
    originEid: opts?.originEid,
    origin: opts?.origin,
    followEid,
    fallback: spatial ? 'world' : busName === 'music' ? 'music' : 'ui',
  });

  // World one-shots: skip if listener is past attenuation range (full-volume
  // ghosts used to play across the whole map via 2D playSound).
  if (
    spatial &&
    pos &&
    isBeyondHearRange(pos[0], pos[1], pos[2], maxDistance)
  ) {
    recordAudioDebugEvent({
      kind: 'skip',
      key,
      source: 'bank',
      bus: busName,
      spatial: true,
      pos,
      followEid,
      originEid: originInfo.originEid,
      originName: originInfo.originName,
      origin: originInfo.origin,
      detail: `cull>${maxDistance}m`,
    });
    return NULL_HANDLE;
  }

  const h = ensureHowl(key, def, spatial);
  if (!h) return NULL_HANDLE;

  const id = h.play();
  const ap: ActivePlay = {
    key,
    howl: h,
    id,
    baseVolume: (def.volume ?? 1) * (opts?.volume ?? 1),
    busName,
    loop,
    spatial,
    startedAt:
      typeof performance !== 'undefined' ? performance.now() : Date.now(),
    followEid,
    originEid: originInfo.originEid,
    originName: originInfo.originName,
    origin: originInfo.origin,
  };
  h.rate(opts?.pitch ?? def.pitch ?? 1, id);
  h.volume(gainFor(ap), id);
  if (pos) h.pos(pos[0], pos[1], pos[2], id);
  active.add(ap);

  recordAudioDebugEvent({
    kind: 'play',
    key,
    source: 'bank',
    bus: busName,
    volume: ap.baseVolume,
    loop,
    spatial,
    followEid,
    originEid: ap.originEid,
    originName: ap.originName,
    origin: ap.origin,
    pos,
    howlId: id,
  });

  // One-shots remove themselves; loops live until stopped.
  if (!loop) {
    h.once(
      'end',
      () => {
        active.delete(ap);
        recordAudioDebugEvent({
          kind: 'end',
          key: ap.key,
          source: 'bank',
          bus: ap.busName,
          howlId: ap.id,
          originEid: ap.originEid,
          originName: ap.originName,
          origin: ap.origin,
        });
      },
      id
    );
  }
  return makeHandle(ap);
}

/**
 * Allow Howl construction for preloads (call from a user-gesture handler).
 * Flushes any keys queued by {@link preloadSounds} before unlock.
 */
export function allowSoundPreload(): void {
  if (soundPreloadAllowed) return;
  soundPreloadAllowed = true;
  if (pendingSoundPreload.size === 0) return;
  const keys = [...pendingSoundPreload];
  pendingSoundPreload.clear();
  preloadSounds(keys);
}

/**
 * Warm Howl decode/cache without playing. Prefer this over
 * ``playSound(key, { volume: 0 })`` + stop — that polluted the profiler with
 * fake play/stop pairs and only warmed the 2D cache (spatial defs still cold).
 *
 * For ``def.spatial`` keys, warms the spatial Howl (what ``playSoundAt`` uses).
 * In the browser, defers until {@link allowSoundPreload} so Howler does not
 * create an AudioContext before a user gesture (autoplay policy warning).
 */
export function preloadSounds(keys?: readonly string[]): void {
  if (!audioEnabled) return;
  const list = keys ?? [...bank.keys()];
  if (!soundPreloadAllowed) {
    for (const key of list) {
      if (!bank.has(key)) {
        logger.warn(`[audio] preloadSounds: unknown key "${key}"`);
        continue;
      }
      pendingSoundPreload.add(key);
    }
    return;
  }
  for (const key of list) {
    const def = bank.get(key);
    if (!def) {
      logger.warn(`[audio] preloadSounds: unknown key "${key}"`);
      continue;
    }
    const wantSpatial = !!def.spatial;
    const h = ensureHowl(key, def, wantSpatial);
    if (!h) continue;
    // Howler fetches/decodes on construction when preload:true; load() is idempotent.
    h.load();
    recordAudioDebugEvent({
      kind: 'preload',
      key,
      source: 'bank',
      bus: def.bus ?? 'sfx',
      spatial: wantSpatial,
      origin: 'boot/preload',
      detail: wantSpatial ? 'spatial cache' : '2d cache',
    });
  }
}

/** Fire a 2D (non-positional) sound. Overlapping calls layer freely. */
export function playSound(key: string, opts?: PlayOptions): SoundHandle {
  return playInternal(key, opts, undefined, undefined);
}

/** Fire a spatial one-shot anchored at a world position. */
export function playSoundAt(
  key: string,
  x: number,
  y: number,
  z: number,
  opts?: PlayOptions
): SoundHandle {
  return playInternal(key, opts, undefined, [x, y, z]);
}

/** Fire a spatial sound that follows an entity (repositioned each frame). */
export function playSoundOn(
  eid: number,
  key: string,
  opts?: PlayOptions
): SoundHandle {
  return playInternal(key, opts, eid, undefined);
}

/** Active plays bound to a followed entity (consumed by SoundBankSystem). */
export function getFollowingPlays(): {
  followEid: number;
  setPos: (x: number, y: number, z: number) => void;
}[] {
  const out: {
    followEid: number;
    setPos: (x: number, y: number, z: number) => void;
  }[] = [];
  for (const ap of active) {
    if (ap.followEid !== undefined) {
      out.push({
        followEid: ap.followEid,
        setPos: (x, y, z) => {
          if (ap.lastX === x && ap.lastY === y && ap.lastZ === z) return;
          ap.lastX = x;
          ap.lastY = y;
          ap.lastZ = z;
          ap.howl.pos(x, y, z, ap.id);
        },
      });
    }
  }
  return out;
}

/**
 * Reposition follow-bound plays without allocating a play list each frame.
 * Skips Howler.pos when the entity pose is unchanged.
 */
export function syncFollowingPlayPositions(
  readPos: (eid: number) => { x: number; y: number; z: number } | null
): void {
  for (const ap of active) {
    if (ap.followEid === undefined) continue;
    const p = readPos(ap.followEid);
    if (!p) continue;
    if (ap.lastX === p.x && ap.lastY === p.y && ap.lastZ === p.z) continue;
    ap.lastX = p.x;
    ap.lastY = p.y;
    ap.lastZ = p.z;
    ap.howl.pos(p.x, p.y, p.z, ap.id);
  }
}

/** Drop active plays that follow an entity which no longer exists. */
export function pruneFollowingPlays(exists: (eid: number) => boolean): void {
  for (const ap of active) {
    if (ap.followEid !== undefined && !exists(ap.followEid)) {
      ap.howl.stop(ap.id);
      active.delete(ap);
      recordAudioDebugEvent({
        kind: 'stop',
        key: ap.key,
        source: 'bank',
        bus: ap.busName,
        howlId: ap.id,
        followEid: ap.followEid,
        detail: 'prune missing entity',
      });
    }
  }
}

/** Live bank plays for the profiler Audio tab. */
export function listActiveBankPlays(): AudioDebugActivePlay[] {
  const t = typeof performance !== 'undefined' ? performance.now() : Date.now();
  const out: AudioDebugActivePlay[] = [];
  for (const ap of active) {
    out.push({
      key: ap.key,
      bus: ap.busName,
      volume: ap.baseVolume,
      loop: ap.loop,
      spatial: ap.spatial,
      followEid: ap.followEid,
      originEid: ap.originEid,
      originName: ap.originName,
      origin: ap.origin,
      howlId: ap.id,
      startedAt: ap.startedAt,
      ageMs: t - ap.startedAt,
    });
  }
  return out;
}

export function listBusDebugState(): {
  name: string;
  volume: number;
  muted: boolean;
}[] {
  return [...buses.entries()].map(([name, b]) => ({
    name,
    volume: b.volume,
    muted: b.muted,
  }));
}

/** Stop every active bank play (profiler / panic mute). */
export function stopAllBankPlays(): void {
  for (const ap of [...active]) {
    ap.howl.stop(ap.id);
    active.delete(ap);
    recordAudioDebugEvent({
      kind: 'stop',
      key: ap.key,
      source: 'bank',
      bus: ap.busName,
      howlId: ap.id,
      detail: 'stopAll',
    });
  }
}

// Wire debug snapshot providers once (module load).
setAudioDebugProviders({
  listActive: listActiveBankPlays,
  listBuses: listBusDebugState,
  getMaster: () => masterVolume,
});

// ── Animation-pinned sounds ───────────────────────────────────────────────────

export interface ClipSoundMarker {
  /** Normalized time within the clip (0..1) at which to fire. */
  at: number;
  /** Bank key to play. */
  sound: string;
  volume?: number;
  pitch?: number;
  /** Play positionally on the animated entity (footsteps, swings…). */
  spatial?: boolean;
}

const clipMarkers = new Map<string, ClipSoundMarker[]>();

/** Pin a bank sound to a normalized time within an animation clip. */
export function addClipSound(clipName: string, marker: ClipSoundMarker): void {
  let list = clipMarkers.get(clipName);
  if (!list) {
    list = [];
    clipMarkers.set(clipName, list);
  }
  list.push(marker);
  list.sort((a, b) => a.at - b.at);
}

export function getClipSounds(clipName: string): ClipSoundMarker[] | undefined {
  return clipMarkers.get(clipName);
}

/** Fire markers crossed between two normalized times for one entity's clip. */
export function fireClipMarkers(
  eid: number,
  clipName: string,
  prevNorm: number,
  nextNorm: number
): void {
  const markers = clipMarkers.get(clipName);
  if (!markers) return;
  const wrapped = nextNorm < prevNorm; // clip looped this frame
  for (const m of markers) {
    const crossed = wrapped
      ? m.at > prevNorm || m.at <= nextNorm
      : m.at > prevNorm && m.at <= nextNorm;
    if (!crossed) continue;
    const opts: PlayOptions = { volume: m.volume, pitch: m.pitch };
    if (m.spatial) playSoundOn(eid, m.sound, opts);
    else playSound(m.sound, opts);
  }
}

// ── Test helpers ──────────────────────────────────────────────────────────────

export function _resetSoundBank(): void {
  for (const h of howls.values()) h.unload();
  for (const h of howlsSpatial.values()) h.unload();
  bank.clear();
  howls.clear();
  howlsSpatial.clear();
  buses.clear();
  active.clear();
  clipMarkers.clear();
  pendingSoundPreload.clear();
  soundPreloadAllowed =
    typeof document === 'undefined' ||
    typeof document.addEventListener !== 'function';
  masterVolume = 1;
  listenerValid = false;
}
