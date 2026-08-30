import { registerDebugAction } from 'aigamekit-vibegame';
import type { State } from 'aigamekit-vibegame';

/**
 * Wire the engine ProfilerPlugin into the game's QA surface (DEV-gated by the
 * DebugPlugin registry). Invoke via:
 *   __VIBEGAME__.debug.callAction('profilerTop', 15)
 *   __VIBEGAME__.profiler.top(15)
 *
 * Shared by every example that registers the profiler — the per-game boot
 * code used to re-implement this action verbatim.
 */
export function registerProfilerDebug(state: State): void {
  registerDebugAction(
    state,
    'profilerTop',
    (...args: unknown[]) => {
      const n = typeof args[0] === 'number' ? args[0] : 15;
      const w = window as unknown as {
        __VIBEGAME__?: { profiler?: { top: (k?: number) => unknown } };
      };
      return w.__VIBEGAME__?.profiler?.top(n);
    },
    {
      description: 'Return top profiler systems (__VIBEGAME__.profiler.top)',
    }
  );
}
