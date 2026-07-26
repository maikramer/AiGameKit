import { describe, expect, it } from 'bun:test';
import {
  isProfilerTabId,
  parseProfilerUrl,
} from '../../../src/plugins/profiler/url';

describe('parseProfilerUrl', () => {
  it('opens sample mode for ?profiler=1', () => {
    expect(parseProfilerUrl('?profiler=1')).toEqual({
      mode: 'sample',
      tab: 'systems',
      audioDebug: false,
    });
  });

  it('opens deep mode for ?profiler=deep', () => {
    expect(parseProfilerUrl('?profiler=deep')).toEqual({
      mode: 'deep',
      tab: 'systems',
      audioDebug: false,
    });
  });

  it('opens audio tab for ?profiler=audio', () => {
    expect(parseProfilerUrl('?profiler=audio')).toEqual({
      mode: 'sample',
      tab: 'audio',
      audioDebug: true,
    });
  });

  it('opens world tab for ?profiler=world', () => {
    expect(parseProfilerUrl('?profiler=world')).toEqual({
      mode: 'sample',
      tab: 'world',
      audioDebug: false,
    });
  });

  it('honours profilerTab=audio with profiler=1', () => {
    expect(parseProfilerUrl('?profiler=1&profilerTab=audio')).toEqual({
      mode: 'sample',
      tab: 'audio',
      audioDebug: true,
    });
  });

  it('honours profilerTab=world with profiler=1', () => {
    expect(parseProfilerUrl('?profiler=1&profilerTab=world')).toEqual({
      mode: 'sample',
      tab: 'world',
      audioDebug: false,
    });
  });

  it('disables for profiler=off', () => {
    expect(parseProfilerUrl('?profiler=off').mode).toBeNull();
  });

  it('returns null mode when profiler absent', () => {
    expect(parseProfilerUrl('').mode).toBeNull();
    expect(parseProfilerUrl('?foo=1').mode).toBeNull();
  });
});

describe('isProfilerTabId', () => {
  it('accepts systems, audio, and world', () => {
    expect(isProfilerTabId('systems')).toBe(true);
    expect(isProfilerTabId('audio')).toBe(true);
    expect(isProfilerTabId('world')).toBe(true);
    expect(isProfilerTabId('gpu')).toBe(false);
  });
});
