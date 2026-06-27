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

// Imported after the howler mock is registered (bank.ts imports howler).
const { emitEvent } = await import('../../../src/plugins/rpg-core/events');
const { AudioSource, MusicLayerComponent } = await import(
  '../../../src/plugins/audio/components'
);
const mixer = await import('../../../src/plugins/audio/mixer');
const bank = await import('../../../src/plugins/audio/bank');

function makeLayer(state: State, layer: number, baseVolume: number): number {
  const eid = state.createEntity();
  state.addComponent(eid, MusicLayerComponent);
  state.addComponent(eid, AudioSource);
  MusicLayerComponent.layer[eid] = layer;
  MusicLayerComponent.volume[eid] = baseVolume;
  MusicLayerComponent.fade[eid] = 0;
  AudioSource.loop[eid] = 1;
  AudioSource.volume[eid] = 0;
  return eid;
}

describe('MusicMixer', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    bank._resetSoundBank();
    mixer._resetAudioMix(state);
  });

  describe('volume tiers', () => {
    it('stores master/music/sfx tiers in the audio mix', () => {
      mixer.setMasterVolume(state, 0.8);
      mixer.setMusicVolume(state, 0.6);
      mixer.setSfxVolume(state, 0.4);

      expect(mixer.getMasterVolume(state)).toBeCloseTo(0.8, 5);
      expect(mixer.getMusicVolume(state)).toBeCloseTo(0.6, 5);
      expect(mixer.getSfxVolume(state)).toBeCloseTo(0.4, 5);

      const mix = mixer.getAudioMix(state);
      expect(mix.master).toBeCloseTo(0.8, 5);
      expect(mix.music).toBeCloseTo(0.6, 5);
      expect(mix.sfx).toBeCloseTo(0.4, 5);
    });

    it('clamps tier values to 0..1', () => {
      mixer.setMasterVolume(state, 2);
      mixer.setMusicVolume(state, -1);
      expect(mixer.getMasterVolume(state)).toBe(1);
      expect(mixer.getMusicVolume(state)).toBe(0);
    });
  });

  describe('setMusicVolume applies', () => {
    it('multiplies the active layer by the music tier (base 0.7 * music 0.5 = 0.35)', () => {
      mixer.setMasterVolume(state, 1);
      mixer.setMusicVolume(state, 1);

      const explore = makeLayer(state, mixer.MUSIC_LAYER_EXPLORE, 0.7);
      mixer.playMusicLayer(state, 'explore');
      mixer.MusicMixerSystem.update!(state);
      expect(AudioSource.volume[explore]).toBeCloseTo(0.7, 5);

      mixer.setMusicVolume(state, 0.5);
      mixer.MusicMixerSystem.update!(state);
      // effective volume = base * fade * music_tier * master_tier
      expect(AudioSource.volume[explore]).toBeCloseTo(0.35, 5);
    });
  });

  describe('crossfade explore->battle on event', () => {
    it('crossfades explore->battle when MUSIC_ENTER_BATTLE is emitted', () => {
      mixer.setMasterVolume(state, 1);
      mixer.setMusicVolume(state, 0.7);

      const explore = makeLayer(state, mixer.MUSIC_LAYER_EXPLORE, 0.7);
      const battle = makeLayer(state, mixer.MUSIC_LAYER_BATTLE, 0.9);

      mixer.playMusicLayer(state, 'explore');
      mixer.MusicMixerSystem.update!(state);

      expect(MusicLayerComponent.fade[explore]).toBeCloseTo(1, 5);
      expect(AudioSource.volume[explore]).toBeCloseTo(0.7 * 0.7, 5);
      expect(MusicLayerComponent.fade[battle]).toBeCloseTo(0, 5);
      expect(AudioSource.volume[battle]).toBeCloseTo(0, 5);

      emitEvent(state, mixer.MUSIC_ENTER_BATTLE, null);

      state.time.deltaTime = 0.5;
      mixer.MusicMixerSystem.update!(state);
      expect(MusicLayerComponent.fade[explore]).toBeCloseTo(0.75, 5);
      expect(MusicLayerComponent.fade[battle]).toBeCloseTo(0.25, 5);

      mixer.MusicMixerSystem.update!(state);
      mixer.MusicMixerSystem.update!(state);
      mixer.MusicMixerSystem.update!(state);
      expect(MusicLayerComponent.fade[explore]).toBeCloseTo(0, 5);
      expect(MusicLayerComponent.fade[battle]).toBeCloseTo(1, 5);
      expect(AudioSource.volume[explore]).toBeCloseTo(0, 5);
      expect(AudioSource.volume[battle]).toBeCloseTo(0.9 * 0.7, 5);
    });

    it('crossfades back to explore on MUSIC_EXIT_BATTLE', () => {
      mixer.setMasterVolume(state, 1);
      mixer.setMusicVolume(state, 1);

      const explore = makeLayer(state, mixer.MUSIC_LAYER_EXPLORE, 0.7);
      const battle = makeLayer(state, mixer.MUSIC_LAYER_BATTLE, 0.9);

      mixer.playMusicLayer(state, 'battle');
      mixer.MusicMixerSystem.update!(state);
      expect(MusicLayerComponent.fade[battle]).toBeCloseTo(1, 5);

      emitEvent(state, mixer.MUSIC_EXIT_BATTLE, null);
      state.time.deltaTime = 1;
      mixer.MusicMixerSystem.update!(state);
      expect(MusicLayerComponent.fade[battle]).toBeCloseTo(0.5, 5);
      expect(MusicLayerComponent.fade[explore]).toBeCloseTo(0.5, 5);

      mixer.MusicMixerSystem.update!(state);
      mixer.MusicMixerSystem.update!(state);
      expect(MusicLayerComponent.fade[battle]).toBeCloseTo(0, 5);
      expect(MusicLayerComponent.fade[explore]).toBeCloseTo(1, 5);
    });
  });

  describe('crossfadeMusicLayers helper', () => {
    it('crossfades between two named layers over the given duration', () => {
      mixer.setMasterVolume(state, 1);
      mixer.setMusicVolume(state, 1);

      mixer.registerMusicLayerName('menu', mixer.MUSIC_LAYER_CUSTOM);
      const explore = makeLayer(state, mixer.MUSIC_LAYER_EXPLORE, 0.5);
      const menu = makeLayer(state, mixer.MUSIC_LAYER_CUSTOM, 0.5);

      mixer.playMusicLayer(state, 'explore');
      mixer.MusicMixerSystem.update!(state);
      expect(MusicLayerComponent.fade[explore]).toBeCloseTo(1, 5);

      mixer.crossfadeMusicLayers(state, 'explore', 'menu', 1);
      expect(mixer.getAudioMix(state).fadeDuration).toBeCloseTo(1, 5);

      state.time.deltaTime = 0.5;
      mixer.MusicMixerSystem.update!(state);
      mixer.MusicMixerSystem.update!(state);
      expect(MusicLayerComponent.fade[menu]).toBeCloseTo(1, 5);
      expect(MusicLayerComponent.fade[explore]).toBeCloseTo(0, 5);
    });
  });

  describe('headless', () => {
    it('is a no-op under headless state', () => {
      state.headless = true;
      const explore = makeLayer(state, mixer.MUSIC_LAYER_EXPLORE, 0.7);
      mixer.playMusicLayer(state, 'explore');
      mixer.MusicMixerSystem.update!(state);
      expect(AudioSource.volume[explore]).toBe(0);
    });
  });
});
