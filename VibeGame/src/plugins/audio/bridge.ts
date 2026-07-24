/**
 * Browser QA bridge: ``window.__VIBEGAME__.audio`` for Playwright / AI.
 * Uses the same bank singleton as gameplay (no dual-module import).
 */
import {
  clearAudioDebugLog,
  getAudioDebugSnapshot,
  type AudioDebugSnapshot,
} from './debug-log';
import {
  getAudioListenerWorldPos,
  playSound,
  playSoundAt,
  setAudioListenerWorldPos,
  stopAllBankPlays,
  type PlayOptions,
  type SoundHandle,
} from './bank';

export interface VibeGameAudioHandle {
  playSound(key: string, opts?: PlayOptions): SoundHandle;
  playSoundAt(
    key: string,
    x: number,
    y: number,
    z: number,
    opts?: PlayOptions
  ): SoundHandle;
  stopAll(): void;
  /** Clear gameplay log events. Pass ``{ keepPreload: true }`` to keep boot cache rows. */
  clearLog(opts?: { keepPreload?: boolean }): void;
  snapshot(): AudioDebugSnapshot;
  setListenerPos(x: number, y: number, z: number): void;
  getListenerPos(): { x: number; y: number; z: number } | null;
}

export function createAudioHandle(): VibeGameAudioHandle {
  return {
    playSound,
    playSoundAt,
    stopAll: stopAllBankPlays,
    clearLog: (opts) => clearAudioDebugLog(opts),
    snapshot: getAudioDebugSnapshot,
    setListenerPos: setAudioListenerWorldPos,
    getListenerPos: getAudioListenerWorldPos,
  };
}

/**
 * Attach ``window.__VIBEGAME__.audio``.
 * Does **not** create ``__VIBEGAME__`` — DebugPlugin owns that object; creating
 * an empty stub here would make DebugPlugin skip the real ECS bridge.
 */
export function installAudioBridge(): VibeGameAudioHandle {
  const handle = createAudioHandle();
  if (typeof window === 'undefined') return handle;
  const w = window as unknown as { __VIBEGAME__?: Record<string, unknown> };
  if (!w.__VIBEGAME__) return handle;
  w.__VIBEGAME__.audio = handle;
  return handle;
}

/** Idempotent: attach audio handle once the debug bridge exists. */
export function ensureAudioBridge(): void {
  if (typeof window === 'undefined') return;
  const w = window as unknown as { __VIBEGAME__?: Record<string, unknown> };
  if (!w.__VIBEGAME__ || w.__VIBEGAME__.audio) return;
  installAudioBridge();
}
