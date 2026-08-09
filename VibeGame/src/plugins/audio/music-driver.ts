import { Howler } from 'howler';
import type { State, System } from '../../core';
import {
  crossfadeMusicLayers,
  playMusicLayer,
  resolveMusicLayer,
} from './mixer';

/**
 * Data-driven music layer driver.
 *
 * Replaces the hand-rolled BGM systems games used to write (poll the game
 * state, wait for the AudioContext to unlock, then play/stop `bgm-*` keys
 * with a debounce). A game declares one `<MusicLayer>` entity per layer in
 * its scene XML and drives them with a single resolver:
 *
 * ```ts
 * withSystem(createMusicLayerDriver({
 *   resolve: (state) => (anyCreatureAggro() ? 'battle' : 'explore'),
 *   debounceMs: 1000,
 * }));
 * ```
 *
 * The driver gates on the user-gesture audio unlock, crossfades between
 * layers (never overlaps two tracks) and damps rapid oscillation.
 */

export interface MusicLayerDriverOptions {
  /** Resolve the desired layer from game state. */
  resolve: (state: State) => string | number;
  /**
   * Minimum milliseconds between switches — damps oscillation (e.g. combat
   * aggro flapping). Default 0 (switch immediately).
   */
  debounceMs?: number;
  /** Crossfade duration in seconds. Default 2 (the mixer's own default). */
  fade?: number;
  /** Layer to play on the first unlocked tick. Default: first resolve(). */
  initial?: string | number;
}

let activeLayer: number | null = null;

/** Last layer the driver started (null until the audio unlocks). */
export function getActiveMusicLayer(): number | null {
  return activeLayer;
}

// Browsers keep the AudioContext suspended until a user gesture; Howler
// queues plays on a suspended context but they may not resume reliably.
// Headless (tests/CLI) has no AudioContext at all and must not be gated.
function audioUnlocked(): boolean {
  return typeof window === 'undefined' || Howler.ctx?.state === 'running';
}

export function createMusicLayerDriver(
  options: MusicLayerDriverOptions
): System {
  const debounceMs = options.debounceMs ?? 0;
  const fade = options.fade ?? 2;
  let started = false;
  let lastSwitchAt = 0;

  return {
    name: 'MusicLayerDriverSystem',
    group: 'simulation',
    update(state: State): void {
      if (!audioUnlocked()) return;

      const target = resolveMusicLayer(options.resolve(state));
      const now = performance.now();
      const initial =
        options.initial !== undefined
          ? resolveMusicLayer(options.initial)
          : target;

      if (!started) {
        started = true;
        // Allow the first *switch* immediately — the debounce only damps
        // oscillation between switches, never the initial play (same
        // semantics as the per-game BGM systems this driver replaces).
        lastSwitchAt = now - debounceMs;
        activeLayer = initial;
        playMusicLayer(state, initial);
        return;
      }
      if (target === activeLayer || now - lastSwitchAt < debounceMs) return;

      const prev = activeLayer;
      lastSwitchAt = now;
      activeLayer = target;
      crossfadeMusicLayers(state, prev ?? target, target, fade);
    },
  };
}
