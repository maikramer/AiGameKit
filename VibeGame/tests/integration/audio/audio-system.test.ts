import {
  afterAll,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  mock,
} from 'bun:test';
import { State } from 'vibegame';
import { AudioEmitter } from '../../../src/plugins/audio/components';
import { AudioPlugin } from '../../../src/plugins/audio/plugin';

const originalWarn = console.warn;
beforeAll(() => {
  console.warn = (...args: unknown[]) => {
    const msg = String(args[0] ?? '');
    if (msg.includes('[audio]') && msg.includes('AudioListener')) return;
    originalWarn.apply(console, args as []);
  };
});
afterAll(() => {
  console.warn = originalWarn;
});

const howlInstances: any[] = [];

class MockHowl {
  play = mock(() => {});
  stop = mock(() => {});
  unload = mock(() => {});
  volume = mock(() => {});
  rate = mock(() => {});
  loop = mock(() => {});
  pos = mock(() => {});
  pannerAttr = mock(() => {});
  _opts: any;
  _onEnd?: () => void;
  on = mock((event: string, cb: () => void) => {
    if (event === 'end') this._onEnd = cb;
  });
  constructor(opts: any) {
    this._opts = opts;
    howlInstances.push(this);
  }
}

mock.module('howler', () => ({ Howl: MockHowl }));

const {
  registerAudioClip,
  AudioSystem,
  playAudioEmitter,
  clearAudioClipRegistry,
} = await import('../../../src/plugins/audio/systems');

describe('AudioSystem Integration', () => {
  let state: State;

  beforeEach(() => {
    clearAudioClipRegistry();
    howlInstances.length = 0;
  });

  it('should register an audio clip in the registry', () => {
    registerAudioClip(1, '/assets/test.mp3');
    registerAudioClip(2, '/assets/boom.mp3');

    state = new State();
    state.registerPlugin(AudioPlugin);
    const entity = state.createEntity();
    state.addComponent(entity, AudioEmitter);
    AudioEmitter.clipPath[entity] = 1;

    expect(AudioEmitter.clipPath[entity]).toBe(1);
  });

  it('should return early when state.headless is true', () => {
    howlInstances.length = 0;
    registerAudioClip(1, '/assets/test.mp3');

    state = new State();
    state.registerPlugin(AudioPlugin);
    state.headless = true;

    const entity = state.createEntity();
    state.addComponent(entity, AudioEmitter);
    AudioEmitter.clipPath[entity] = 1;
    AudioEmitter.playing[entity] = 1;

    AudioSystem.update!(state);

    expect(howlInstances).toHaveLength(0);
  });

  it('should not crash when update runs with no audio entities', () => {
    state = new State();
    state.registerPlugin(AudioPlugin);
    AudioSystem.update!(state);
  });

  it('should not crash when entity has no clip registered', () => {
    howlInstances.length = 0;
    state = new State();
    state.registerPlugin(AudioPlugin);

    const entity = state.createEntity();
    state.addComponent(entity, AudioEmitter);
    AudioEmitter.clipPath[entity] = 999;
    AudioEmitter.playing[entity] = 1;

    AudioSystem.update!(state);
    expect(howlInstances).toHaveLength(0);
  });

  it('should create a Howl and call play when playing transitions 0->1', () => {
    howlInstances.length = 0;
    registerAudioClip(1, '/assets/test.mp3');

    state = new State();
    state.registerPlugin(AudioPlugin);

    const entity = state.createEntity();
    state.addComponent(entity, AudioEmitter);
    AudioEmitter.clipPath[entity] = 1;
    AudioEmitter.volume[entity] = 0.8;
    AudioEmitter.playing[entity] = 0;

    AudioSystem.update!(state);
    expect(howlInstances).toHaveLength(0);

    AudioEmitter.playing[entity] = 1;
    AudioSystem.update!(state);

    expect(howlInstances).toHaveLength(1);
    expect(howlInstances[0].play).toHaveBeenCalledTimes(1);
  });

  it('should call stop when playing transitions 1->0', () => {
    howlInstances.length = 0;
    registerAudioClip(1, '/assets/test.mp3');

    state = new State();
    state.registerPlugin(AudioPlugin);

    const entity = state.createEntity();
    state.addComponent(entity, AudioEmitter);
    AudioEmitter.clipPath[entity] = 1;
    AudioEmitter.volume[entity] = 0.5;
    AudioEmitter.playing[entity] = 1;

    AudioSystem.update!(state);
    expect(howlInstances).toHaveLength(1);
    expect(howlInstances[0].play).toHaveBeenCalledTimes(1);

    AudioEmitter.playing[entity] = 0;
    AudioSystem.update!(state);

    expect(howlInstances[0].stop).toHaveBeenCalledTimes(1);
  });

  it('should pass spatial options when spatial=1', () => {
    howlInstances.length = 0;
    registerAudioClip(1, '/assets/test.mp3');

    state = new State();
    state.registerPlugin(AudioPlugin);

    const entity = state.createEntity();
    state.addComponent(entity, AudioEmitter);
    AudioEmitter.clipPath[entity] = 1;
    AudioEmitter.playing[entity] = 1;
    AudioEmitter.spatial[entity] = 1;
    AudioEmitter.minDistance[entity] = 5;
    AudioEmitter.maxDistance[entity] = 200;
    AudioEmitter.rolloff[entity] = 2;

    AudioSystem.update!(state);

    expect(howlInstances).toHaveLength(1);
    expect(howlInstances[0]._opts.pos).toBeDefined();
    expect(howlInstances[0]._opts.pannerAttr.refDistance).toBe(5);
    expect(howlInstances[0]._opts.pannerAttr.maxDistance).toBe(200);
    expect(howlInstances[0]._opts.pannerAttr.rolloffFactor).toBe(2);
  });

  it('should sync playing to 0 on Howl end for non-loop clips', () => {
    howlInstances.length = 0;
    registerAudioClip(1, '/assets/test.mp3');

    state = new State();
    state.registerPlugin(AudioPlugin);

    const entity = state.createEntity();
    state.addComponent(entity, AudioEmitter);
    AudioEmitter.clipPath[entity] = 1;
    AudioEmitter.loop[entity] = 0;
    AudioEmitter.playing[entity] = 1;

    AudioSystem.update!(state);
    expect(howlInstances).toHaveLength(1);
    expect(AudioEmitter.playing[entity]).toBe(1);

    howlInstances[0]._onEnd?.();
    expect(AudioEmitter.playing[entity]).toBe(0);
  });

  it('should not call Howl loop() every frame for looped clips (Howler restarts playback)', () => {
    howlInstances.length = 0;
    registerAudioClip(1, '/assets/bgm.mp3');

    state = new State();
    state.registerPlugin(AudioPlugin);

    const entity = state.createEntity();
    state.addComponent(entity, AudioEmitter);
    AudioEmitter.clipPath[entity] = 1;
    AudioEmitter.volume[entity] = 0.5;
    AudioEmitter.loop[entity] = 1;
    AudioEmitter.playing[entity] = 1;

    for (let i = 0; i < 25; i++) {
      AudioSystem.update!(state);
    }

    expect(howlInstances[0].loop).toHaveBeenCalledTimes(0);
  });

  it('should call stop and play on playAudioEmitter for existing non-loop Howl', () => {
    howlInstances.length = 0;
    registerAudioClip(1, '/assets/test.mp3');

    state = new State();
    state.registerPlugin(AudioPlugin);

    const entity = state.createEntity();
    state.addComponent(entity, AudioEmitter);
    AudioEmitter.clipPath[entity] = 1;
    AudioEmitter.loop[entity] = 0;
    AudioEmitter.playing[entity] = 1;

    AudioSystem.update!(state);
    expect(howlInstances).toHaveLength(1);

    playAudioEmitter(state, entity);
    expect(howlInstances[0].stop).toHaveBeenCalled();
    expect(howlInstances[0].play).toHaveBeenCalledTimes(2);
  });
});
