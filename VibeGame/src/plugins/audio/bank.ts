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
  /** When set, FollowEmitterSystem repositions this play to the entity each frame. */
  followEid?: number;
}

const bank = new Map<string, SoundDef>();
const howls = new Map<string, Howl>();
const buses = new Map<string, BusState>();
const active = new Set<ActivePlay>();

let masterVolume = 1;
// Disabled under headless (node/tests) where there is no Web Audio context.
let audioEnabled = typeof window !== 'undefined';

function clamp01(v: number): number {
  return v < 0 ? 0 : v > 1 ? 1 : v;
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
    const existing = howls.get(key);
    if (existing) {
      existing.unload();
      howls.delete(key);
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
}

export function isBusMuted(name: string): boolean {
  return bus(name).muted;
}

/** Enable/disable all bank playback (engine forces off under headless). */
export function setAudioEnabled(enabled: boolean): void {
  audioEnabled = enabled;
}

// ── Playback ─────────────────────────────────────────────────────────────────

function ensureHowl(key: string, def: SoundDef): Howl | null {
  if (!audioEnabled) return null;
  let h = howls.get(key);
  if (!h) {
    h = new Howl({
      src: [def.url],
      preload: true,
      loop: def.loop ?? false,
      volume: def.volume ?? 1,
      rate: def.pitch ?? 1,
      ...(def.spatial && {
        pannerAttr: {
          refDistance: def.minDistance ?? 1,
          maxDistance: def.maxDistance ?? 100,
          rolloffFactor: def.rolloff ?? 1,
        },
      }),
    });
    howls.set(key, h);
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
    },
    fadeIn(toVolume: number, seconds: number) {
      ap.baseVolume = clamp01(toVolume);
      ap.howl.fade(0, gainFor(ap), Math.max(0, seconds) * 1000, ap.id);
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
    console.warn(`[audio] playSound: unknown key "${key}"`);
    return NULL_HANDLE;
  }
  const h = ensureHowl(key, def);
  if (!h) return NULL_HANDLE;

  const id = h.play();
  const busName = opts?.bus ?? def.bus ?? 'sfx';
  const ap: ActivePlay = {
    key,
    howl: h,
    id,
    baseVolume: (def.volume ?? 1) * (opts?.volume ?? 1),
    busName,
    followEid,
  };
  h.rate(opts?.pitch ?? def.pitch ?? 1, id);
  h.volume(gainFor(ap), id);
  if (pos) h.pos(pos[0], pos[1], pos[2], id);
  active.add(ap);

  // One-shots remove themselves; loops live until stopped.
  if (!(def.loop ?? false)) {
    h.once('end', () => active.delete(ap), id);
  }
  return makeHandle(ap);
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

/** Active plays bound to a followed entity (consumed by FollowEmitterSystem). */
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
        setPos: (x, y, z) => ap.howl.pos(x, y, z, ap.id),
      });
    }
  }
  return out;
}

/** Drop active plays that follow an entity which no longer exists. */
export function pruneFollowingPlays(exists: (eid: number) => boolean): void {
  for (const ap of active) {
    if (ap.followEid !== undefined && !exists(ap.followEid)) {
      ap.howl.stop(ap.id);
      active.delete(ap);
    }
  }
}

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
  bank.clear();
  howls.clear();
  buses.clear();
  active.clear();
  clipMarkers.clear();
  masterVolume = 1;
}
