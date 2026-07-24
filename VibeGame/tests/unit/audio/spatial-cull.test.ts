import { beforeEach, describe, expect, it, mock } from 'bun:test';

let nextId = 1;
const howlInstances: { spatial?: boolean; play: ReturnType<typeof mock> }[] =
  [];

class MockHowl {
  _opts: Record<string, unknown>;
  play = mock(() => nextId++);
  stop = mock(() => {});
  unload = mock(() => {});
  load = mock(function (this: MockHowl) {
    return this;
  });
  volume = mock(() => {});
  rate = mock(() => {});
  once = mock(() => {});
  fade = mock(() => {});
  pos = mock(() => {});
  constructor(opts: Record<string, unknown>) {
    this._opts = opts;
    howlInstances.push({
      spatial: !!(opts as { pannerAttr?: unknown }).pannerAttr,
      play: this.play,
    });
  }
}

mock.module('howler', () => ({
  Howl: MockHowl,
  Howler: { pos: () => {}, ctx: { state: 'running' } },
}));

const bank = await import('../../../src/plugins/audio/bank');
const debug = await import('../../../src/plugins/audio/debug-log');

describe('spatial play cull + howl cache', () => {
  beforeEach(() => {
    bank._resetSoundBank();
    debug._resetAudioDebugLog();
    bank.setAudioEnabled(true);
    howlInstances.length = 0;
    nextId = 1;
    bank.defineSoundBank({
      boom: {
        url: '/boom.ogg',
        volume: 1,
        spatial: true,
        maxDistance: 20,
      },
    });
  });

  it('culls playSoundAt beyond maxDistance', () => {
    bank.setAudioListenerWorldPos(0, 0, 0);
    const far = bank.playSoundAt('boom', 100, 0, 0);
    expect(far.id).toBe(-1);
    expect(howlInstances).toHaveLength(0);
    const skips = debug.getAudioDebugEvents().filter((e) => e.kind === 'skip');
    expect(skips.length).toBe(1);
    expect(skips[0]!.detail).toContain('cull');
  });

  it('plays playSoundAt inside maxDistance with spatial Howl', () => {
    bank.setAudioListenerWorldPos(0, 0, 0);
    const near = bank.playSoundAt('boom', 5, 0, 0);
    expect(near.id).toBeGreaterThan(0);
    expect(howlInstances).toHaveLength(1);
    expect(howlInstances[0]!.spatial).toBe(true);
  });

  it('keeps bare playSound 2D even when def.spatial is true', () => {
    bank.setAudioListenerWorldPos(0, 0, 0);
    const h = bank.playSound('boom');
    expect(h.id).toBeGreaterThan(0);
    expect(howlInstances[0]!.spatial).toBe(false);
  });

  it('does not let 2D preload poison spatial Howl', () => {
    bank.playSound('boom', { volume: 0 }).stop();
    bank.setAudioListenerWorldPos(0, 0, 0);
    bank.playSoundAt('boom', 3, 0, 0);
    expect(howlInstances).toHaveLength(2);
    expect(howlInstances[0]!.spatial).toBe(false);
    expect(howlInstances[1]!.spatial).toBe(true);
  });

  it('preloadSounds warms spatial cache without play/stop', () => {
    bank.preloadSounds(['boom']);
    expect(howlInstances).toHaveLength(1);
    expect(howlInstances[0]!.spatial).toBe(true);
    expect(howlInstances[0]!.play).not.toHaveBeenCalled();
    const events = debug.getAudioDebugEvents();
    expect(events.some((e) => e.kind === 'preload' && e.key === 'boom')).toBe(
      true
    );
    expect(events.some((e) => e.kind === 'play')).toBe(false);
    expect(events.find((e) => e.kind === 'preload')?.origin).toBe(
      'boot/preload'
    );

    bank.setAudioListenerWorldPos(0, 0, 0);
    bank.playSoundAt('boom', 3, 0, 0);
    // Same spatial Howl reused — no second construction.
    expect(howlInstances).toHaveLength(1);
  });

  it('records originEid/originName on play and skip', () => {
    debug.setAudioEntityNameProvider((eid) =>
      eid === 42 ? 'goblin' : undefined
    );
    bank.setAudioListenerWorldPos(0, 0, 0);
    bank.playSoundAt('boom', 5, 0, 0, { originEid: 42 });
    bank.playSoundAt('boom', 100, 0, 0, { originEid: 42 });
    const events = debug.getAudioDebugEvents();
    const play = events.find((e) => e.kind === 'play');
    const skip = events.find((e) => e.kind === 'skip');
    expect(play?.origin).toBe('goblin#42');
    expect(play?.originEid).toBe(42);
    expect(play?.originName).toBe('goblin');
    expect(skip?.origin).toBe('goblin#42');
    const snap = debug.getAudioDebugSnapshot();
    expect(snap.topOrigins.some((o) => o.origin === 'goblin#42')).toBe(true);
    expect(snap.preloadCount).toBe(0);
  });
});
