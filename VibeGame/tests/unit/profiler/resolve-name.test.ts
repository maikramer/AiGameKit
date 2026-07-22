import { describe, expect, it, beforeEach } from 'bun:test';
import type { System } from '../../../src/core';
import {
  _resetProfilerForTests,
  defineSystem,
  getProfilerOrigin,
  noteSystemRegistration,
  resetProfiler,
  resolveSystemName,
} from '../../../src/core/profiler';

describe('resolveSystemName', () => {
  beforeEach(() => {
    _resetProfilerForTests();
  });

  it('uses explicit system.name', () => {
    const system: System = {
      name: 'TerrainMeshSystem',
      update() {},
    };
    expect(resolveSystemName(system)).toBe('TerrainMeshSystem');
  });

  it('falls back to named update function', () => {
    function fancyUpdate() {}
    const system: System = { update: fancyUpdate };
    expect(resolveSystemName(system)).toBe('fancyUpdate');
  });

  it('unnamed systems get origin-based labels instead of anon#N', () => {
    const system: System = {
      update() {},
    };
    noteSystemRegistration(system);
    const name = resolveSystemName(system);
    expect(name.startsWith('anon#')).toBe(false);
    expect(name.startsWith('unnamed:') || name.length > 0).toBe(true);
  });

  it('defineSystem records origin for the panel', () => {
    const system = defineSystem({
      name: 'FakeGltfSystem',
      update() {},
    });
    expect(resolveSystemName(system)).toBe('FakeGltfSystem');
    const origin = getProfilerOrigin('FakeGltfSystem');
    expect(origin).not.toBe('unknown');
    expect(origin.includes('.ts') || origin.includes('profiler')).toBe(true);
  });

  it('resetProfiler keeps origins', () => {
    defineSystem({
      name: 'KeepOriginSystem',
      update() {},
    });
    expect(getProfilerOrigin('KeepOriginSystem')).not.toBe('unknown');
    resetProfiler();
    expect(getProfilerOrigin('KeepOriginSystem')).not.toBe('unknown');
  });
});
