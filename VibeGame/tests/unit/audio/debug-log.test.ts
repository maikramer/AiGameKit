import { beforeEach, describe, expect, it, mock } from 'bun:test';

mock.module('howler', () => ({
  Howl: class {},
  Howler: { ctx: { state: 'running' } },
}));

const debug = await import('../../../src/plugins/audio/debug-log');

describe('audio debug-log', () => {
  beforeEach(() => {
    debug._resetAudioDebugLog();
  });

  it('records events in a ring and clears', () => {
    debug.recordAudioDebugEvent({
      kind: 'play',
      key: 'coin',
      source: 'bank',
      bus: 'sfx',
    });
    debug.recordAudioDebugEvent({
      kind: 'stop',
      key: 'coin',
      source: 'bank',
    });
    const events = debug.getAudioDebugEvents();
    expect(events).toHaveLength(2);
    expect(events[0]!.kind).toBe('play');
    expect(events[1]!.kind).toBe('stop');
    debug.clearAudioDebugLog();
    expect(debug.getAudioDebugEvents()).toHaveLength(0);
  });

  it('overflows oldest events past RING_SIZE', () => {
    for (let i = 0; i < 300; i++) {
      debug.recordAudioDebugEvent({
        kind: 'play',
        key: `s${i}`,
        source: 'bank',
      });
    }
    const events = debug.getAudioDebugEvents();
    expect(events).toHaveLength(256);
    expect(events[0]!.key).toBe('s44');
    expect(events[events.length - 1]!.key).toBe('s299');
  });

  it('captures caller only when armed', () => {
    debug.armAudioDebug(false);
    debug.recordAudioDebugEvent({
      kind: 'play',
      key: 'a',
      source: 'bank',
    });
    expect(debug.getAudioDebugEvents()[0]!.caller).toBeUndefined();

    debug.armAudioDebug(true);
    debug.recordAudioDebugEvent({
      kind: 'play',
      key: 'b',
      source: 'bank',
    });
    const withStack = debug.getAudioDebugEvents().find((e) => e.key === 'b');
    expect(withStack?.caller).toBeDefined();
    expect(withStack!.caller!.length).toBeGreaterThan(0);
  });

  it('arms from URL profiler=audio', () => {
    expect(debug.armAudioDebugFromUrl('?profiler=audio')).toBe(true);
    expect(debug.isAudioDebugArmed()).toBe(true);
  });

  it('arms from profilerTab=audio', () => {
    debug._resetAudioDebugLog();
    expect(debug.armAudioDebugFromUrl('?profiler=1&profilerTab=audio')).toBe(
      true
    );
    expect(debug.isAudioDebugArmed()).toBe(true);
  });

  it('snapshot reports top keys and unknown', () => {
    debug.recordAudioDebugEvent({
      kind: 'play',
      key: 'foot',
      source: 'bank',
    });
    debug.recordAudioDebugEvent({
      kind: 'play',
      key: 'foot',
      source: 'bank',
    });
    debug.recordAudioDebugEvent({
      kind: 'unknown',
      key: 'nope',
      source: 'bank',
    });
    const snap = debug.getAudioDebugSnapshot();
    expect(snap.topKeys[0]).toEqual({ key: 'foot', count: 2 });
    expect(snap.unknownKeys).toContain('nope');
  });
});
