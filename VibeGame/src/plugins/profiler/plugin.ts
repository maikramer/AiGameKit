import type { Plugin, State, System } from '../../core';
import { defineSystem, disableProfiler, enableProfiler, getProfilerMode, isProfilerEnabled, setProfilerMode, toggleProfilerFreeze } from '../../core/profiler';
import { installProfilerBridge } from './handle';
import {
  createProfilerPanel,
  destroyProfilerPanel,
  refreshProfilerPanel,
  setProfilerPanelVisible,
  type ProfilerPanelRuntime,
} from './panel';

const panelByState = new WeakMap<State, ProfilerPanelRuntime>();
const keyHandlerByState = new WeakMap<State, (event: KeyboardEvent) => void>();

function isTextInput(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return (
    target.isContentEditable ||
    target.tagName === 'INPUT' ||
    target.tagName === 'TEXTAREA'
  );
}

function ensurePanel(state: State): ProfilerPanelRuntime | null {
  if (state.headless || typeof document === 'undefined') return null;
  let runtime = panelByState.get(state);
  if (!runtime) {
    runtime = createProfilerPanel();
    panelByState.set(state, runtime);
  }
  return runtime;
}

function openProfiler(state: State, mode: 'sample' | 'deep' = 'sample'): void {
  enableProfiler(mode);
  const panel = ensurePanel(state);
  if (panel) setProfilerPanelVisible(panel, true);
}

function closeProfiler(state: State): void {
  const panel = panelByState.get(state);
  if (panel) setProfilerPanelVisible(panel, false);
  disableProfiler();
}

function toggleProfilerPanel(state: State): void {
  const panel = ensurePanel(state);
  if (!panel) {
    if (isProfilerEnabled()) disableProfiler();
    else enableProfiler('sample');
    return;
  }
  if (panel.visible) {
    closeProfiler(state);
  } else {
    openProfiler(state, getProfilerMode() === 'deep' ? 'deep' : 'sample');
  }
}

function cycleProfilerMode(): void {
  if (!isProfilerEnabled()) {
    enableProfiler('sample');
    return;
  }
  const next = getProfilerMode() === 'deep' ? 'sample' : 'deep';
  setProfilerMode(next);
}

function parseUrlProfilerFlag(): 'sample' | 'deep' | null {
  if (typeof window === 'undefined') return null;
  try {
    const params = new URLSearchParams(window.location.search);
    const raw = params.get('profiler');
    if (raw === null) return null;
    const v = raw.trim().toLowerCase();
    if (v === '' || v === '1' || v === 'true' || v === 'sample') return 'sample';
    if (v === 'deep') return 'deep';
    if (v === '0' || v === 'false' || v === 'off') return null;
    return 'sample';
  } catch {
    return null;
  }
}

function buildKeyHandler(state: State): (event: KeyboardEvent) => void {
  return (event: KeyboardEvent) => {
    const inPanelFilter =
      event.target instanceof HTMLElement &&
      event.target.closest('#vibegame-profiler-panel') !== null &&
      (event.target.tagName === 'INPUT' || event.target.tagName === 'TEXTAREA');

    if (event.code === 'Pause') {
      if (isTextInput(event.target) && !inPanelFilter) return;
      event.preventDefault();
      if (!isProfilerEnabled()) openProfiler(state);
      toggleProfilerFreeze();
      return;
    }

    if (inPanelFilter || isTextInput(event.target)) return;

    if (event.key === 'p' || event.key === 'P') {
      if (event.shiftKey) {
        event.preventDefault();
        if (!isProfilerEnabled()) openProfiler(state, 'deep');
        else cycleProfilerMode();
        const panel = ensurePanel(state);
        if (panel && !panel.visible) setProfilerPanelVisible(panel, true);
      } else {
        event.preventDefault();
        toggleProfilerPanel(state);
      }
    }
  };
}

export const ProfilerPanelSystem: System = defineSystem({
  name: 'ProfilerPanelSystem',
  group: 'draw',
  last: true,
  update(state: State): void {
    if (state.headless) return;
    const panel = panelByState.get(state);
    if (!panel) return;
    refreshProfilerPanel(state, panel);
  },
  dispose(state: State): void {
    const handler = keyHandlerByState.get(state);
    if (handler && typeof window !== 'undefined') {
      window.removeEventListener('keydown', handler);
    }
    keyHandlerByState.delete(state);
    const panel = panelByState.get(state);
    if (panel) {
      destroyProfilerPanel(panel);
      panelByState.delete(state);
    }
  },
});

/**
 * Opt-in hierarchical frame profiler: per-system timings, group bars,
 * renderer/terrain/BVH counters, Chrome User Timing (`deep` mode), and JSON export.
 *
 * Keys (when registered, not headless):
 *   `P` — toggle panel (enables sample mode)
 *   `Shift+P` — cycle sample ↔ deep
 *   `Pause` — freeze / unfreeze snapshot
 *
 * URL: `?profiler=1` or `?profiler=deep` opens on start.
 *
 * Bridge: `window.__VIBEGAME__.profiler`
 */
export const ProfilerPlugin: Plugin = {
  systems: [ProfilerPanelSystem],
  initialize(state: State): void {
    installProfilerBridge();

    if (typeof window !== 'undefined' && !state.headless) {
      const handler = buildKeyHandler(state);
      keyHandlerByState.set(state, handler);
      window.addEventListener('keydown', handler);

      const urlMode = parseUrlProfilerFlag();
      if (urlMode) {
        openProfiler(state, urlMode);
      }
    }
  },
};
