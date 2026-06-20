import { logger } from '../../core/utils/logger';
import { defineQuery } from '../../core';
import type { Adapter, Parser, Recipe, State, System } from '../../core';
import { onEvent } from '../rpg-core/events';
import { setMasterVolume as bank_setMasterVolume, setBusVolume } from './bank';
import { AudioSource, MusicLayerComponent } from './components';

export const MUSIC_LAYER_EXPLORE = 0;
export const MUSIC_LAYER_BATTLE = 1;
export const MUSIC_LAYER_CUSTOM = 2;

export const MUSIC_ENTER_BATTLE = 'music:enter-battle';
export const MUSIC_EXIT_BATTLE = 'music:exit-battle';

export { MusicLayerComponent };

export interface AudioMix {
  master: number;
  music: number;
  sfx: number;
  activeLayer: number;
  fadeDuration: number;
}

const DEFAULT_MIX: AudioMix = {
  master: 1,
  music: 0.7,
  sfx: 0.8,
  activeLayer: MUSIC_LAYER_EXPLORE,
  fadeDuration: 2,
};

const MIX_STATE = new WeakMap<State, AudioMix>();

export function getAudioMix(state: State): AudioMix {
  let mix = MIX_STATE.get(state);
  if (!mix) {
    mix = { ...DEFAULT_MIX };
    MIX_STATE.set(state, mix);
  }
  return mix;
}

export function _resetAudioMix(state: State): void {
  MIX_STATE.set(state, { ...DEFAULT_MIX });
}

function clamp01(v: number): number {
  return v < 0 ? 0 : v > 1 ? 1 : v;
}

const layerNames = new Map<string, number>([
  ['explore', MUSIC_LAYER_EXPLORE],
  ['battle', MUSIC_LAYER_BATTLE],
]);

export function registerMusicLayerName(name: string, id: number): void {
  layerNames.set(name.toLowerCase(), id);
}

export function resolveMusicLayer(name: string | number): number {
  if (typeof name === 'number') return name;
  const asNum = Number(name);
  if (!Number.isNaN(asNum)) return asNum;
  const mapped = layerNames.get(name.toLowerCase());
  if (mapped === undefined) {
    logger.warn(
      `[audio] unknown music layer "${name}", falling back to explore`
    );
    return MUSIC_LAYER_EXPLORE;
  }
  return mapped;
}

export function setMasterVolume(state: State, v: number): void {
  const mix = getAudioMix(state);
  mix.master = clamp01(v);
  bank_setMasterVolume(mix.master);
}

export function getMasterVolume(state: State): number {
  return getAudioMix(state).master;
}

export function setMusicVolume(state: State, v: number): void {
  const mix = getAudioMix(state);
  mix.music = clamp01(v);
  setBusVolume('music', mix.music);
}

export function getMusicVolume(state: State): number {
  return getAudioMix(state).music;
}

export function setSfxVolume(state: State, v: number): void {
  const mix = getAudioMix(state);
  mix.sfx = clamp01(v);
  setBusVolume('sfx', mix.sfx);
}

export function getSfxVolume(state: State): number {
  return getAudioMix(state).sfx;
}

const musicLayerQuery = defineQuery([MusicLayerComponent]);

export function playMusicLayer(state: State, layer: string | number): void {
  const mix = getAudioMix(state);
  mix.activeLayer = resolveMusicLayer(layer);
  for (const eid of musicLayerQuery(state.world)) {
    const isActive = MusicLayerComponent.layer[eid] === mix.activeLayer;
    MusicLayerComponent.fade[eid] = isActive ? 1 : 0;
    AudioSource.playing[eid] = 1;
  }
}

export function crossfadeMusicLayers(
  state: State,
  _from: string | number,
  to: string | number,
  duration: number
): void {
  const mix = getAudioMix(state);
  mix.activeLayer = resolveMusicLayer(to);
  mix.fadeDuration = Math.max(0.0001, duration);
}

const wired = new WeakSet<State>();

export function wireMusicMixerEvents(state: State): void {
  if (wired.has(state)) return;
  wired.add(state);
  onEvent(state, MUSIC_ENTER_BATTLE, () => {
    getAudioMix(state).activeLayer = MUSIC_LAYER_BATTLE;
  });
  onEvent(state, MUSIC_EXIT_BATTLE, () => {
    getAudioMix(state).activeLayer = MUSIC_LAYER_EXPLORE;
  });
}

export const MusicMixerSystem: System = {
  group: 'simulation',
  update(state: State): void {
    if (state.headless) return;
    wireMusicMixerEvents(state);

    const mix = getAudioMix(state);
    const dt = state.time.deltaTime;
    const step = mix.fadeDuration > 0 ? Math.min(1, dt / mix.fadeDuration) : 1;

    for (const eid of musicLayerQuery(state.world)) {
      const target = MusicLayerComponent.layer[eid] === mix.activeLayer ? 1 : 0;
      let fade = MusicLayerComponent.fade[eid];
      if (fade < target) fade = Math.min(target, fade + step);
      else if (fade > target) fade = Math.max(target, fade - step);
      MusicLayerComponent.fade[eid] = fade;
      AudioSource.volume[eid] =
        MusicLayerComponent.volume[eid] * fade * mix.music * mix.master;
    }
  },
};

function num(value: string | number): number {
  return typeof value === 'number' ? value : Number(value);
}

export const musicLayerRecipe: Recipe = {
  name: 'MusicLayer',
  components: ['audioSource', 'music-layer'],
  parserAttributes: ['layer', 'base-volume'],
};

export const musicLayerAdapters: Record<string, Adapter> = {
  layer: (entity: number, value: string) => {
    MusicLayerComponent.layer[entity] = resolveMusicLayer(value);
  },
  'base-volume': (entity: number, value: string) => {
    MusicLayerComponent.volume[entity] = clamp01(num(value));
  },
};

export const audioMixerRecipe: Recipe = {
  name: 'AudioMixer',
  parserAttributes: ['master', 'music', 'sfx'],
};

export const audioMixerParser: Parser = ({ element, state }): void => {
  const a = element.attributes;
  if (a.master != null)
    setMasterVolume(state, num(a.master as string | number));
  if (a.music != null) setMusicVolume(state, num(a.music as string | number));
  if (a.sfx != null) setSfxVolume(state, num(a.sfx as string | number));
};
