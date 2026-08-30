import { beforeEach, describe, expect, it, mock } from 'bun:test';
import { State } from 'aigamekit-vibegame';
import { HOWLER_GLOBAL_STUB } from '../../helpers/howler-stub';

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
  Howler: { ...HOWLER_GLOBAL_STUB },
}));

import {
  MUSIC_LAYER_BATTLE,
  MUSIC_LAYER_CUSTOM,
  MUSIC_LAYER_EXPLORE,
  MUSIC_ENTER_BATTLE,
  MUSIC_EXIT_BATTLE,
  crossfadeMusicLayers,
  getAudioMix,
  getMasterVolume,
  getMusicVolume,
  getSfxVolume,
  playMusicLayer,
  registerMusicLayerName,
  resolveMusicLayer,
  setMasterVolume,
  setMusicVolume,
  setSfxVolume,
  _resetAudioMix,
} from '../../../src/plugins/audio/mixer';
import {
  _resetSoundBank,
  defineSoundBank,
  getBusVolume,
  getMasterVolume as bankMaster,
  getSoundDef,
  isBusMuted,
  setAudioEnabled,
  setBusMuted,
  setBusVolume,
  setMasterVolume as setBankMaster,
} from '../../../src/plugins/audio/bank';
import {
  AudioListener,
  AudioSource,
  MusicLayerComponent,
} from '../../../src/plugins/audio/components';

describe('audio matrix: mixer volumes', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    _resetAudioMix(state);
  });

  for (const [label, setter, getter, value] of [
    ['master', setMasterVolume, getMasterVolume, 0.75],
    ['music', setMusicVolume, getMusicVolume, 0.5],
    ['sfx', setSfxVolume, getSfxVolume, 0.25],
  ] as const) {
    it(`${label} volume roundtrip`, () => {
      setter(state, value);
      expect(getter(state)).toBeCloseTo(value, 5);
    });
  }

  it('getAudioMix returns shared object', () => {
    const mix = getAudioMix(state);
    setMasterVolume(state, 0.33);
    expect(mix.master).toBeCloseTo(0.33, 5);
  });

  it('playMusicLayer sets active layer id', () => {
    playMusicLayer(state, MUSIC_LAYER_EXPLORE);
    expect(getAudioMix(state).activeLayer).toBe(MUSIC_LAYER_EXPLORE);
  });

  it('crossfadeMusicLayers updates fade duration', () => {
    crossfadeMusicLayers(state, MUSIC_LAYER_EXPLORE, MUSIC_LAYER_BATTLE, 0.5);
    const mix = getAudioMix(state);
    expect(mix.activeLayer).toBe(MUSIC_LAYER_BATTLE);
    expect(mix.fadeDuration).toBeCloseTo(0.5, 5);
  });
});

describe('audio matrix: music layer constants', () => {
  it('explore layer id 0', () => expect(MUSIC_LAYER_EXPLORE).toBe(0));
  it('battle layer id 1', () => expect(MUSIC_LAYER_BATTLE).toBe(1));
  it('custom layer id 2', () => expect(MUSIC_LAYER_CUSTOM).toBe(2));
  it('enter battle event string', () =>
    expect(MUSIC_ENTER_BATTLE).toBe('music:enter-battle'));
  it('exit battle event string', () =>
    expect(MUSIC_EXIT_BATTLE).toBe('music:exit-battle'));
});

describe('audio matrix: resolveMusicLayer', () => {
  it('numeric passthrough', () => {
    expect(resolveMusicLayer(2)).toBe(2);
  });
  it('named explore', () => {
    expect(resolveMusicLayer('explore')).toBe(MUSIC_LAYER_EXPLORE);
  });
  it('named battle', () => {
    expect(resolveMusicLayer('battle')).toBe(MUSIC_LAYER_BATTLE);
  });
  it('custom registered name', () => {
    registerMusicLayerName('town', 7);
    expect(resolveMusicLayer('town')).toBe(7);
  });
  it('unknown name falls back to explore', () => {
    expect(resolveMusicLayer('nonexistent-layer-xyz')).toBe(
      MUSIC_LAYER_EXPLORE
    );
  });
});

describe('audio matrix: sound bank', () => {
  beforeEach(() => {
    _resetSoundBank();
  });

  it('defineSoundBank registers defs', () => {
    defineSoundBank({ footstep: { url: '/sfx/step.wav', bus: 'sfx' } });
    expect(getSoundDef('footstep')?.url).toBe('/sfx/step.wav');
  });

  it('bus volume defaults and override', () => {
    setBusVolume('sfx', 0.6);
    expect(getBusVolume('sfx')).toBeCloseTo(0.6, 5);
  });

  it('bus mute toggle', () => {
    setBusMuted('music', true);
    expect(isBusMuted('music')).toBe(true);
    setBusMuted('music', false);
    expect(isBusMuted('music')).toBe(false);
  });

  it('setBankMaster affects bankMaster getter', () => {
    setBankMaster(0.42);
    expect(bankMaster()).toBeCloseTo(0.42, 5);
  });

  it('setAudioEnabled toggles without throw', () => {
    setAudioEnabled(false);
    setAudioEnabled(true);
    expect(bankMaster()).toBeGreaterThanOrEqual(0);
  });

  for (const key of ['ui', 'sfx', 'music', 'voice']) {
    it(`bus ${key} volume is finite`, () => {
      expect(Number.isFinite(getBusVolume(key))).toBe(true);
    });
  }
});

describe('audio matrix: components exist', () => {
  it('AudioSource fields', () => {
    expect(AudioSource.volume).toBeDefined();
    expect(AudioSource.loop).toBeDefined();
  });
  it('AudioListener fields', () => {
    expect(AudioListener.posX).toBeDefined();
  });
  it('MusicLayerComponent fields', () => {
    expect(MusicLayerComponent.layer).toBeDefined();
  });
});

describe('audio matrix: volume clamp sanity', () => {
  let state: State;
  beforeEach(() => {
    state = new State();
    _resetAudioMix(state);
  });

  for (const v of [0, 0.1, 0.5, 1]) {
    it(`master accepts ${v}`, () => {
      setMasterVolume(state, v);
      expect(getMasterVolume(state)).toBeCloseTo(v, 5);
    });
  }
});
