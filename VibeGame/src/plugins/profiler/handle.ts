import {
  copyProfilerSnapshot,
  disableProfiler,
  downloadProfilerSnapshot,
  enableProfiler,
  freezeProfiler,
  getProfilerMode,
  getProfilerSnapshot,
  getProfilerTop,
  isProfilerEnabled,
  isProfilerFrozen,
  resetProfiler,
  setProfilerMode,
  toggleProfilerFreeze,
  unfreezeProfiler,
  type ProfilerMode,
  type ProfilerSnapshot,
  type ProfilerTimingStats,
} from '../../core/profiler';

export interface VibeGameProfilerHandle {
  enable(mode?: Exclude<ProfilerMode, 'off'>): void;
  disable(): void;
  isEnabled(): boolean;
  getMode(): ProfilerMode;
  setMode(mode: ProfilerMode): void;
  snapshot(): ProfilerSnapshot;
  top(n?: number): ProfilerTimingStats[];
  freeze(): ProfilerSnapshot;
  unfreeze(): void;
  toggleFreeze(): boolean;
  isFrozen(): boolean;
  reset(): void;
  download(filename?: string): ProfilerSnapshot;
  copy(): Promise<boolean>;
}

export function createProfilerHandle(): VibeGameProfilerHandle {
  return {
    enable(mode = 'sample') {
      enableProfiler(mode);
    },
    disable() {
      disableProfiler();
    },
    isEnabled() {
      return isProfilerEnabled();
    },
    getMode() {
      return getProfilerMode();
    },
    setMode(mode) {
      setProfilerMode(mode);
    },
    snapshot() {
      return getProfilerSnapshot();
    },
    top(n = 15) {
      return getProfilerTop(n);
    },
    freeze() {
      return freezeProfiler();
    },
    unfreeze() {
      unfreezeProfiler();
    },
    toggleFreeze() {
      return toggleProfilerFreeze();
    },
    isFrozen() {
      return isProfilerFrozen();
    },
    reset() {
      resetProfiler();
    },
    download(filename) {
      return downloadProfilerSnapshot(filename);
    },
    copy() {
      return copyProfilerSnapshot();
    },
  };
}

/** Attach or replace `window.__VIBEGAME__.profiler`. */
export function installProfilerBridge(): VibeGameProfilerHandle {
  const handle = createProfilerHandle();
  if (typeof window === 'undefined') return handle;
  const w = window as unknown as {
    __VIBEGAME__?: Record<string, unknown>;
  };
  if (!w.__VIBEGAME__) {
    w.__VIBEGAME__ = {};
  }
  w.__VIBEGAME__.profiler = handle;
  return handle;
}
