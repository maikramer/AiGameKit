import { afterEach, beforeEach, describe, expect, it } from 'bun:test';
import {
  _resetProfilerForTests,
  disableProfiler,
  enableProfiler,
  getProfilerMode,
  isProfilerEnabled,
} from '../../../../src/core/profiler';
import {
  createProfilerHandle,
  installProfilerBridge,
  type VibeGameProfilerHandle,
} from '../../../../src/plugins/profiler/handle';
import { ProfilerPlugin } from '../../../../src/plugins/profiler/plugin';

describe('Profiler plugin matrix — structure', () => {
  it('ProfilerPlugin exposes ProfilerPanelSystem', () => {
    expect(ProfilerPlugin.systems).toHaveLength(1);
    expect(ProfilerPlugin.systems![0].name).toBe('ProfilerPanelSystem');
  });

  it('ProfilerPanelSystem is in draw group', () => {
    expect(ProfilerPlugin.systems![0].group).toBe('draw');
  });

  it('ProfilerPanelSystem is marked last', () => {
    expect(ProfilerPlugin.systems![0].last).toBe(true);
  });
});

describe('Profiler handle matrix — enable modes', () => {
  beforeEach(() => {
    _resetProfilerForTests();
  });

  afterEach(() => {
    _resetProfilerForTests();
  });

  for (let i = 0; i < 25; i++) {
    it(`handle.enable sample #${i}`, () => {
      const h = createProfilerHandle();
      h.enable('sample');
      expect(h.isEnabled()).toBe(true);
      expect(h.getMode()).toBe('sample');
      h.disable();
      expect(h.isEnabled()).toBe(false);
    });

    it(`handle.enable deep #${i}`, () => {
      const h = createProfilerHandle();
      h.enable('deep');
      expect(h.getMode()).toBe('deep');
      h.disable();
    });
  }
});

describe('Profiler handle matrix — setMode', () => {
  beforeEach(() => {
    _resetProfilerForTests();
    enableProfiler('sample');
  });

  for (let i = 0; i < 20; i++) {
    it(`handle.setMode deep then sample #${i}`, () => {
      const h = createProfilerHandle();
      h.setMode('deep');
      expect(h.getMode()).toBe('deep');
      h.setMode('sample');
      expect(h.getMode()).toBe('sample');
    });
  }
});

describe('Profiler handle matrix — snapshot shape', () => {
  beforeEach(() => {
    _resetProfilerForTests();
    enableProfiler('sample');
  });

  for (let i = 0; i < 20; i++) {
    it(`snapshot arrays defined #${i}`, () => {
      const snap = createProfilerHandle().snapshot();
      expect(Array.isArray(snap.systems)).toBe(true);
      expect(Array.isArray(snap.groups)).toBe(true);
      expect(Array.isArray(snap.customs)).toBe(true);
      expect(typeof snap.fps).toBe('number');
    });
  }
});

describe('Profiler handle matrix — top(n)', () => {
  beforeEach(() => {
    _resetProfilerForTests();
    enableProfiler('sample');
  });

  for (let n = 1; n <= 15; n++) {
    it(`handle.top(${n}) length bound`, () => {
      const rows = createProfilerHandle().top(n);
      expect(rows.length).toBeLessThanOrEqual(n);
    });
  }
});

describe('Profiler handle matrix — bridge install', () => {
  for (let i = 0; i < 10; i++) {
    it(`installProfilerBridge returns handle #${i}`, () => {
      const h = installProfilerBridge();
      expect(typeof h.enable).toBe('function');
      expect(typeof h.snapshot).toBe('function');
    });
  }
});

describe('Profiler handle matrix — reset while enabled', () => {
  beforeEach(() => {
    _resetProfilerForTests();
    enableProfiler('deep');
  });

  for (let i = 0; i < 10; i++) {
    it(`reset keeps deep mode #${i}`, () => {
      const h = createProfilerHandle();
      h.reset();
      expect(isProfilerEnabled()).toBe(true);
      expect(getProfilerMode()).toBe('deep');
      disableProfiler();
    });
  }
});

describe('Profiler handle matrix — freeze API', () => {
  beforeEach(() => {
    _resetProfilerForTests();
    enableProfiler('sample');
  });

  let handle: VibeGameProfilerHandle;

  beforeEach(() => {
    handle = createProfilerHandle();
  });

  for (let i = 0; i < 10; i++) {
    it(`toggleFreeze boolean #${i}`, () => {
      expect(handle.isFrozen()).toBe(false);
      expect(handle.toggleFreeze()).toBe(true);
      expect(handle.isFrozen()).toBe(true);
      handle.unfreeze();
      expect(handle.isFrozen()).toBe(false);
    });
  }
});
