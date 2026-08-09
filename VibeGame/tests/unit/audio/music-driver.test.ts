import { beforeEach, describe, expect, it, mock } from 'bun:test';
import { State } from '../../../src/core';

mock.module('howler', () => ({
  Howl: class MockHowl {
    play = mock(() => 1);
    stop = mock(() => {});
    volume = mock(() => {});
    rate = mock(() => {});
    loop = mock(() => {});
    pos = mock(() => {});
    pannerAttr = mock(() => {});
    fade = mock(() => {});
    once = mock(() => {});
    unload = mock(() => {});
  },
  Howler: { pos: () => {}, ctx: { state: 'running', resume: () => {} } },
}));

// Imported after the howler mock is registered.
const { createMusicLayerDriver, getActiveMusicLayer } =
  await import('../../../src/plugins/audio/music-driver');
const mixer = await import('../../../src/plugins/audio/mixer');

describe('MusicLayerDriver', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    mixer._resetAudioMix(state);
  });

  it('plays the resolved layer on the first tick', () => {
    const system = createMusicLayerDriver({
      resolve: () => 'explore',
    });
    system.update!(state);

    expect(getActiveMusicLayer()).toBe(mixer.MUSIC_LAYER_EXPLORE);
    expect(mixer.getAudioMix(state).activeLayer).toBe(
      mixer.MUSIC_LAYER_EXPLORE
    );
  });

  it('honours `initial` for the first play before any resolve', () => {
    const system = createMusicLayerDriver({
      resolve: () => 'battle',
      initial: 'explore',
    });
    system.update!(state);

    expect(getActiveMusicLayer()).toBe(mixer.MUSIC_LAYER_EXPLORE);
  });

  it('crossfades when the resolved layer changes', () => {
    let combat = false;
    const system = createMusicLayerDriver({
      resolve: () => (combat ? 'battle' : 'explore'),
    });
    system.update!(state);

    combat = true;
    system.update!(state);

    expect(getActiveMusicLayer()).toBe(mixer.MUSIC_LAYER_BATTLE);
  });

  it('does not re-switch while the resolved layer is unchanged', () => {
    const system = createMusicLayerDriver({
      resolve: () => 'explore',
    });
    system.update!(state);
    const mix = mixer.getAudioMix(state);

    system.update!(state);
    expect(mix.activeLayer).toBe(mixer.MUSIC_LAYER_EXPLORE);
  });

  it('damps oscillation with debounceMs', () => {
    let combat = false;
    const system = createMusicLayerDriver({
      resolve: () => (combat ? 'battle' : 'explore'),
      debounceMs: 1000,
    });
    system.update!(state); // explore

    combat = true;
    system.update!(state); // battle, switch allowed
    expect(getActiveMusicLayer()).toBe(mixer.MUSIC_LAYER_BATTLE);

    combat = false;
    system.update!(state); // explore — within debounce window
    expect(getActiveMusicLayer()).toBe(mixer.MUSIC_LAYER_BATTLE);
  });
});
