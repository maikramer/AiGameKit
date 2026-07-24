import type { Plugin, State, System } from '../../core';
import {
  defineSystem,
  disableProfiler,
  enableProfiler,
  getProfilerMode,
  isProfilerEnabled,
  setProfilerMode,
  toggleProfilerFreeze,
} from '../../core/profiler';
import { ensureAudioBridge } from '../audio/bridge';
import { armAudioDebug, armAudioDebugFromUrl } from '../audio/debug-log';
import { installProfilerBridge } from './handle';
import {
  createProfilerPanel,
  destroyProfilerPanel,
  refreshProfilerPanel,
  setProfilerPanelTab,
  setProfilerPanelVisible,
  type ProfilerPanelRuntime,
} from './panel';
import { parseProfilerUrl, type ProfilerTabId } from './url';

const panelByState = new WeakMap<State, ProfilerPanelRuntime>();
const keyHandlerByState = new WeakMap<State, (event: KeyboardEvent) => void>();
const activeTabByState = new WeakMap<State, ProfilerTabId>();

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
    runtime.onTabChange = (tab) => {
      activeTabByState.set(state, tab);
      if (tab === 'audio') armAudioDebug(true);
    };
    const initial = activeTabByState.get(state) ?? 'systems';
    setProfilerPanelTab(runtime, initial, { syncUrl: false, notify: false });
    panelByState.set(state, runtime);
  }
  return runtime;
}

function openProfiler(
  state: State,
  mode: 'sample' | 'deep' = 'sample',
  tab?: ProfilerTabId
): void {
  enableProfiler(mode);
  if (tab) activeTabByState.set(state, tab);
  const panel = ensurePanel(state);
  if (panel) {
    if (tab) setProfilerPanelTab(panel, tab);
    setProfilerPanelVisible(panel, true);
  }
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

/** Current profiler tab for this state (bridge / tests). */
export function getProfilerPanelTab(state: State): ProfilerTabId {
  const panel = panelByState.get(state);
  return panel?.tab ?? activeTabByState.get(state) ?? 'systems';
}

export function setProfilerTabForState(state: State, tab: ProfilerTabId): void {
  activeTabByState.set(state, tab);
  if (tab === 'audio') armAudioDebug(true);
  const panel = panelByState.get(state);
  if (panel) setProfilerPanelTab(panel, tab);
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
    activeTabByState.delete(state);
  },
});

/**
 * Opt-in hierarchical frame profiler: per-system timings, group bars,
 * renderer/terrain/BVH counters, Chrome User Timing (`deep` mode), JSON export,
 * and an Audio debug tab (active plays + ring log from loading onward).
 *
 * Keys (when registered, not headless):
 *   `P` — toggle panel (enables sample mode)
 *   `Shift+P` — cycle sample ↔ deep
 *   `Pause` — freeze / unfreeze snapshot
 *
 * URL:
 *   `?profiler=1` or `?profiler=deep` — open Systems tab
 *   `?profiler=audio` — open Audio tab (arms stack capture)
 *   `?profiler=1&profilerTab=audio` — same
 *
 * Bridge: `window.__VIBEGAME__.profiler` (`getTab` / `setTab` / `audioSnapshot`)
 */
export const ProfilerPlugin: Plugin = {
  systems: [ProfilerPanelSystem],
  initialize(state: State): void {
    installProfilerBridge({
      getTab: () => getProfilerPanelTab(state),
      setTab: (tab) => {
        openProfiler(
          state,
          getProfilerMode() === 'deep' ? 'deep' : 'sample',
          tab
        );
      },
    });
    // Debug bridge is usually already up (DebugPlugin before ProfilerPlugin).
    ensureAudioBridge();

    if (typeof window !== 'undefined' && !state.headless) {
      const handler = buildKeyHandler(state);
      keyHandlerByState.set(state, handler);
      window.addEventListener('keydown', handler);

      // Re-arm if AudioPlugin already ran; capture loading plays with stacks.
      armAudioDebugFromUrl();

      const cfg = parseProfilerUrl();
      if (cfg.audioDebug) armAudioDebug(true);
      if (cfg.mode) {
        activeTabByState.set(state, cfg.tab);
        openProfiler(state, cfg.mode, cfg.tab);
      }
    }
  },
};
