import { logger } from '../../core/utils/logger';
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
import { NamedSfxResolverSystem } from './sfx-registry';
import { installAudioBridge } from './bridge';
import { armAudioDebugFromUrl } from './debug-log';

function audioUrlAdapter(entity: number, value: string, _state: State): void {
  registerAudioClip(entity, value.trim());
  AudioSource.clipPath[entity] = entity;
}

function audioSoundAdapter(entity: number, value: string, _state: State): void {
  const def = getSoundDef(value.trim());
  if (!def) {
    logger.warn(`[audio] <AudioSource sound="${value}">: unknown bank key`);
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
  systems: [
    AudioListenerSetupSystem,
    NamedSfxResolverSystem,
    AudioSystem,
    SoundBankSystem,
    MusicMixerSystem,
  ],
  recipes: [audioClipRecipe, musicLayerRecipe, audioMixerRecipe],
  components: {
    audioSource: AudioSource,
    AudioListener,
    'music-layer': MusicLayerComponent,
  },
  initialize(state: State): void {
    // Arm stack capture before preload/BGM so loading plays are attributed.
    armAudioDebugFromUrl();
    // DebugPlugin may not have created __VIBEGAME__ yet — retry from systems.
    if (typeof window !== 'undefined' && !state.headless) {
      installAudioBridge();
    }
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
    shorthands: {
      // Route the top-level XML attr to the component field: without this,
      // `base-volume="0.9"` never reaches the music-layer adapter (the
      // expander only camelizes against field names, `baseVolume` matches
      // nothing, and the raw attr dies in the recipe's parserAttributes
      // skip) — leaving every layer at the default volume 1.
      'music-layer': { 'base-volume': 'volume' },
    },
    parsers: {
      AudioMixer: audioMixerParser,
    },
  },
};
