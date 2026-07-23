import { afterEach, beforeEach, describe, expect, it } from 'bun:test';
import type { System } from '../../../src/core';
import {
  _resetProfilerForTests,
  defineSystem,
  disableProfiler,
  enableProfiler,
  freezeProfiler,
  getProfilerMode,
  getProfilerSnapshot,
  getProfilerTop,
  isProfilerEnabled,
  isProfilerFrozen,
  noteSystemRegistration,
  resetProfiler,
  resolveSystemName,
  setProfilerMode,
  toggleProfilerFreeze,
  unfreezeProfiler,
} from '../../../src/core/profiler';

describe('Profiler matrix — resolveSystemName labels', () => {
  beforeEach(() => {
    _resetProfilerForTests();
  });

  for (let i = 0; i < 40; i++) {
    it(`explicit name System${i}`, () => {
      const system: System = { name: `System${i}`, update() {} };
      expect(resolveSystemName(system)).toBe(`System${i}`);
    });
  }

  for (let i = 0; i < 40; i++) {
    it(`group label Simulation${i}`, () => {
      const system: System = {
        name: `Sim${i}`,
        group: 'simulation',
        update() {},
      };
      expect(system.group).toBe('simulation');
      expect(resolveSystemName(system)).toBe(`Sim${i}`);
    });
  }
});

describe('Profiler matrix — defineSystem origin', () => {
  beforeEach(() => {
    _resetProfilerForTests();
  });

  for (let i = 0; i < 20; i++) {
    it(`defineSystem Origin${i} resolves name`, () => {
      const sys = defineSystem({
        name: `Origin${i}`,
        update() {},
      });
      expect(resolveSystemName(sys)).toBe(`Origin${i}`);
    });
  }
});

describe('Profiler matrix — getProfilerTop limits', () => {
  beforeEach(() => {
    _resetProfilerForTests();
    enableProfiler('sample');
  });

  afterEach(() => {
    _resetProfilerForTests();
  });

  for (let n = 1; n <= 20; n++) {
    it(`getProfilerTop(${n}) returns at most ${n} rows`, () => {
      const top = getProfilerTop(n);
      expect(top.length).toBeLessThanOrEqual(n);
    });
  }
});

describe('Profiler matrix — mode toggles', () => {
  beforeEach(() => {
    _resetProfilerForTests();
  });

  for (let i = 0; i < 10; i++) {
    it(`sample→deep cycle #${i}`, () => {
      enableProfiler('sample');
      expect(getProfilerMode()).toBe('sample');
      setProfilerMode('deep');
      expect(getProfilerMode()).toBe('deep');
      disableProfiler();
      expect(isProfilerEnabled()).toBe(false);
    });
  }
});

describe('Profiler matrix — freeze toggles', () => {
  beforeEach(() => {
    _resetProfilerForTests();
    enableProfiler('sample');
  });

  for (let i = 0; i < 10; i++) {
    it(`toggleProfilerFreeze round ${i}`, () => {
      expect(isProfilerFrozen()).toBe(false);
      toggleProfilerFreeze();
      expect(isProfilerFrozen()).toBe(true);
      unfreezeProfiler();
      expect(isProfilerFrozen()).toBe(false);
      freezeProfiler();
      expect(isProfilerFrozen()).toBe(true);
      unfreezeProfiler();
    });
  }
});

describe('Profiler matrix — reset clears snapshot', () => {
  beforeEach(() => {
    _resetProfilerForTests();
    enableProfiler('sample');
  });

  for (let i = 0; i < 10; i++) {
    it(`resetProfiler clears window #${i}`, () => {
      resetProfiler();
      expect(getProfilerSnapshot().windowFrames).toBe(0);
      expect(isProfilerEnabled()).toBe(true);
    });
  }
});

describe('Profiler matrix — unnamed registration', () => {
  beforeEach(() => {
    _resetProfilerForTests();
  });

  for (let i = 0; i < 10; i++) {
    it(`unnamed system ${i} avoids anon# prefix`, () => {
      const system: System = { update() {} };
      noteSystemRegistration(system);
      expect(resolveSystemName(system).startsWith('anon#')).toBe(false);
    });
  }
});
