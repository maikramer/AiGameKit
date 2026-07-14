import type { State } from '../../core';
import {
  getAudioMix,
  MUSIC_ENTER_BATTLE,
  MUSIC_EXIT_BATTLE,
  MUSIC_LAYER_BATTLE,
  MUSIC_LAYER_EXPLORE,
} from '../audio/mixer';
import { onEvent } from './events';

const wired = new WeakSet<State>();

/** Wire battle/explore music layer switches to the RPG event bus. Call from RpgCorePlugin. */
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
