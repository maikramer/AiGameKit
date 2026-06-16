import type { Plugin, State } from '../../core';
import { AudioSource, AudioListener } from './components';
import { getSoundDef } from './bank';
import { audioClipRecipe } from './recipes';
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

/** `sound="key"`: seed this emitter from a bank entry (url + defaults). */
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
  systems: [AudioListenerSetupSystem, AudioSystem, SoundBankSystem],
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
        sound: audioSoundAdapter,
      },
    },
  },
};
