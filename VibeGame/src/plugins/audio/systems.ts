import { Howl, Howler } from 'howler';
import { defineQuery } from '../../core';
import type { State, System } from '../../core';
import { AudioEmitter, AudioListener } from './components';
import { MainCamera } from '../rendering/components';
import { TransformHierarchySystem } from '../transforms/systems';
import { Transform, WorldTransform } from '../transforms/components';

// Howler.js spatial audio uses stereo panning only (no HRTF).

const clipRegistry = new Map<number, string>();

export function registerAudioClip(id: number, url: string): void {
  clipRegistry.set(id, url);
}

const audioQuery = defineQuery([AudioEmitter]);
const listenerQuery = defineQuery([AudioListener, MainCamera, WorldTransform]);
const mainCameraTransformQuery = defineQuery([MainCamera, Transform]);

interface AudioState {
  howlMap: Map<number, Howl>;
  prevPlaying: Map<number, number>;
  _listenerWarned?: boolean;
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

/** Garante AudioListener na entidade da câmara (MainCamera + Transform). */
export const AudioListenerSetupSystem: System = {
  group: 'setup',
  update(state: State) {
    if (state.headless) return;
    for (const eid of mainCameraTransformQuery(state.world)) {
      if (!state.hasComponent(eid, AudioListener)) {
        state.addComponent(eid, AudioListener);
        AudioListener.posX[eid] = 0;
        AudioListener.posY[eid] = 0;
        AudioListener.posZ[eid] = 0;
      }
    }
  },
};

export const AudioSystem: System = {
  group: 'simulation',
  after: [TransformHierarchySystem],
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
              pos: [
                Transform.posX[eid],
                Transform.posY[eid],
                Transform.posZ[eid],
              ],
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
          howl.pos(
            Transform.posX[eid],
            Transform.posY[eid],
            Transform.posZ[eid]
          );
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

    const listeners = listenerQuery(state.world);
    if (listeners.length > 0) {
      const eid = listeners[0];
      const x = WorldTransform.posX[eid];
      const y = WorldTransform.posY[eid];
      const z = WorldTransform.posZ[eid];
      AudioListener.posX[eid] = x;
      AudioListener.posY[eid] = y;
      AudioListener.posZ[eid] = z;
      const ctx = Howler.ctx;
      if (ctx && ctx.listener) {
        ctx.listener.positionX.value = x;
        ctx.listener.positionY.value = y;
        ctx.listener.positionZ.value = z;
      }
    } else if (
      audioQuery(state.world).length > 0 &&
      !getOrCreateState(state)._listenerWarned
    ) {
      getOrCreateState(state)._listenerWarned = true;
      console.warn(
        '[audio] Nenhuma entidade com AudioListener + MainCamera + WorldTransform; áudio espacial pode falhar.'
      );
    }
  },
};
