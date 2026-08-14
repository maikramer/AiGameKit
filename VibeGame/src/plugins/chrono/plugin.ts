import {
  ChronoRecorderSystem,
  chronoMark,
  chronoRewind,
  enableChrono,
  getChronoStatus,
  isChronoEnabled,
  onChronoSeek,
  type ChronoOptions,
  type Plugin,
  type State,
} from '../../core';
import { registerDebugAction, registerDebugVar } from '../debug/registry';
import { resyncPhysicsAfterSeek } from './utils';

export interface ChronoPluginOptions extends ChronoOptions {
  /** Enable physics body resync after seeks (default true when physics is present). */
  physicsResync?: boolean;
}

/**
 * Opt-in time-travel recording. Register via the builder for one-line setup:
 *
 * ```ts
 * GAME.withChrono({ seconds: 60, hz: 10 }).run();
 * ```
 *
 * Then rewind from anywhere (console, debug overlay, pause menu):
 * `chronoRewind(state, 5)` restores the world to five seconds ago while
 * keeping entity identities stable for named entities.
 */
export const ChronoPlugin: Plugin = {
  systems: [ChronoRecorderSystem],
  initialize(state: State) {
    if (!isChronoEnabled(state)) {
      enableChrono(state);
    }
  },
};

/** Builder helper: enable chrono with explicit options (see GameBuilder.withChrono). */
export function applyChronoOptions(
  state: State,
  options?: ChronoPluginOptions
): void {
  enableChrono(state, options);
  if (options?.physicsResync !== false) {
    onChronoSeek(state, resyncPhysicsAfterSeek);
  }
  registerDebugAction(
    state,
    'chrono.rewind',
    (seconds?: unknown) => chronoRewind(state, Number(seconds ?? 5)),
    { description: 'Rewind the world N seconds (default 5)' }
  );
  registerDebugAction(
    state,
    'chrono.mark',
    (label?: unknown) =>
      chronoMark(state, typeof label === 'string' ? label : undefined),
    { description: 'Bookmark the current frame' }
  );
  registerDebugAction(state, 'chrono.status', () => getChronoStatus(state), {
    description: 'Recorder status (frames, capacity, marks)',
  });
  registerDebugVar(state, 'chrono.frames', () => getChronoStatus(state).frames);
}
