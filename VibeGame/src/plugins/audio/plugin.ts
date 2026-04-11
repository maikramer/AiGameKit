import type { Plugin, State } from '../../core';
import { AudioSource, AudioListener } from './components';
import { audioClipRecipe } from './recipes';
import {
  AudioListenerSetupSystem,
  AudioSystem,
  registerAudioClip,
} from './systems';

function audioUrlAdapter(entity: number, value: string, _state: State): void {
  registerAudioClip(entity, value.trim());
  AudioSource.clipPath[entity] = entity;
}

export const AudioPlugin: Plugin = {
  systems: [AudioListenerSetupSystem, AudioSystem],
  recipes: [audioClipRecipe],
  components: {
    audioSource: AudioSource,
    AudioListener,
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
    },
    adapters: {
      audioSource: {
        url: audioUrlAdapter,
      },
    },
  },
};
