import type { Plugin, State } from '../../core';
import { AudioSource, AudioListener, MusicLayerComponent } from './components';
import { getSoundDef } from './bank';
import { audioClipRecipe } from './recipes';
import {
  audioMixerParser,
  audioMixerRecipe,
  musicLayerAdapters,
  musicLayerRecipe,
  MusicMixerSystem,
} from './mixer';
import {
  AudioListenerSetupSystem,
  AudioSystem,
  SoundBankSystem,
  registerAudioClip,
} from './systems';

function audioUrlAdapter(entity: number, value: string, _state: State): void {
  registerAudioClip(entity, value.trim());
  AudioSource.clipPath[entity] = entity;
}

function audioSoundAdapter(entity: number, value: string, _state: State): void {
  const def = getSoundDef(value.trim());
  if (!def) {
    console.warn(`[audio] <AudioSource sound="${value}">: unknown bank key`);
    return;
  }
  registerAudioClip(entity, def.url);
  AudioSource.clipPath[entity] = entity;
  AudioSource.volume[entity] = def.volume ?? 1;
  AudioSource.loop[entity] = def.loop ? 1 : 0;
  AudioSource.pitch[entity] = def.pitch ?? 1;
  AudioSource.spatial[entity] = def.spatial ? 1 : 0;
  if (def.minDistance != null)
    AudioSource.minDistance[entity] = def.minDistance;
  if (def.maxDistance != null)
    AudioSource.maxDistance[entity] = def.maxDistance;
  if (def.rolloff != null) AudioSource.rolloff[entity] = def.rolloff;
}

export const AudioPlugin: Plugin = {
  systems: [AudioListenerSetupSystem, AudioSystem, SoundBankSystem, MusicMixerSystem],
  recipes: [audioClipRecipe, musicLayerRecipe, audioMixerRecipe],
  components: {
    audioSource: AudioSource,
    AudioListener,
    'music-layer': MusicLayerComponent,
  },
  config: {
    defaults: {
      audioSource: {
        volume: 1,
        loop: 0,
        pitch: 1,
        spatial: 1,
        minDistance: 1,
        maxDistance: 100,
        rolloff: 1,
        playing: 0,
      },
      'music-layer': {
        layer: 0,
        volume: 1,
        fade: 0,
      },
    },
    adapters: {
      audioSource: {
        url: audioUrlAdapter,
        sound: audioSoundAdapter,
      },
      'music-layer': musicLayerAdapters,
    },
    parsers: {
      AudioMixer: audioMixerParser,
    },
  },
};
