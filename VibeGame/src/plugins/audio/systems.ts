import { Howl } from 'howler';
import { defineQuery } from '../../core';
import type { State, System } from '../../core';
import { AudioEmitter } from './components';
import { Transform } from '../transforms/components';

// Howler.js spatial audio uses stereo panning only (no HRTF).

const clipRegistry = new Map<number, string>();

export function registerAudioClip(id: number, url: string): void {
  clipRegistry.set(id, url);
}

const audioQuery = defineQuery([AudioEmitter]);

interface AudioState {
  howlMap: Map<number, Howl>;
  prevPlaying: Map<number, number>;
}

const AUDIO_STATE = new WeakMap<State, AudioState>();

function getOrCreateState(state: State): AudioState {
  let s = AUDIO_STATE.get(state);
  if (!s) {
    s = { howlMap: new Map(), prevPlaying: new Map() };
    AUDIO_STATE.set(state, s);
  }
  return s;
}

export const AudioSystem: System = {
  group: 'simulation',
  update(state: State) {
    if (state.headless) return;

    const { howlMap, prevPlaying } = getOrCreateState(state);
    const entities = audioQuery(state.world);

    for (const eid of entities) {
      const clipId = AudioEmitter.clipPath[eid];
      const playing = AudioEmitter.playing[eid];
      const wasPlaying = prevPlaying.get(eid) ?? 0;

      let howl = howlMap.get(eid);

      if (playing === 1 && wasPlaying === 0) {
        if (!howl) {
          const url = clipRegistry.get(clipId);
          if (!url) continue;

          const spatial = AudioEmitter.spatial[eid] === 1;
          howl = new Howl({
            src: [url],
            loop: AudioEmitter.loop[eid] === 1,
            volume: AudioEmitter.volume[eid],
            rate: AudioEmitter.pitch[eid],
            ...(spatial && {
              pos: [Transform.posX[eid], Transform.posY[eid], Transform.posZ[eid]],
              pannerAttr: {
                refDistance: AudioEmitter.minDistance[eid],
                maxDistance: AudioEmitter.maxDistance[eid],
                rolloffFactor: AudioEmitter.rolloff[eid],
              },
            }),
          });
          howlMap.set(eid, howl);
        }
        howl.play();
      }

      if (playing === 0 && wasPlaying === 1 && howl) {
        howl.stop();
      }

      if (howl) {
        howl.volume(AudioEmitter.volume[eid]);
        howl.loop(AudioEmitter.loop[eid] === 1);
        howl.rate(AudioEmitter.pitch[eid]);

        if (AudioEmitter.spatial[eid] === 1) {
          howl.pos(Transform.posX[eid], Transform.posY[eid], Transform.posZ[eid]);
          howl.pannerAttr({
            refDistance: AudioEmitter.minDistance[eid],
            maxDistance: AudioEmitter.maxDistance[eid],
            rolloffFactor: AudioEmitter.rolloff[eid],
          });
        }
      }

      prevPlaying.set(eid, playing);
    }

    for (const [eid, howl] of howlMap) {
      if (!state.exists(eid)) {
        howl.unload();
        howlMap.delete(eid);
        prevPlaying.delete(eid);
      }
    }
  },
};
