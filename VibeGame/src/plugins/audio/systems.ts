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

/** Limpa o registo global de clips (útil em testes para evitar fuga entre ficheiros). */
export function clearAudioClipRegistry(): void {
  clipRegistry.clear();
}

const audioQuery = defineQuery([AudioEmitter]);
const listenerQuery = defineQuery([AudioListener, MainCamera, WorldTransform]);
const mainCameraTransformQuery = defineQuery([MainCamera, Transform]);

/** Últimos valores aplicados ao Howl (evitar setters por frame: Howler reinicia reprodução em `loop(true)` com som ativo; `rate()` também altera timers). */
interface HowlPropSnapshot {
  volume: number;
  loop: boolean;
  rate: number;
}

interface AudioState {
  howlMap: Map<number, Howl>;
  prevPlaying: Map<number, number>;
  howlPropSnapshot: Map<number, HowlPropSnapshot>;
  _listenerWarned?: boolean;
}

const AUDIO_STATE = new WeakMap<State, AudioState>();

function getOrCreateState(state: State): AudioState {
  let s = AUDIO_STATE.get(state);
  if (!s) {
    s = {
      howlMap: new Map(),
      prevPlaying: new Map(),
      howlPropSnapshot: new Map(),
    };
    AUDIO_STATE.set(state, s);
  }
  return s;
}

/**
 * Reproduz o clip deste emissor: para one-shots (`loop=0`) reinicia o Howl se já existir;
 * caso contrário define `playing=1` para o próximo tick do AudioSystem criar/reproduzir.
 */
export function playAudioEmitter(state: State, eid: number): void {
  if (state.headless) return;
  const { howlMap, prevPlaying } = getOrCreateState(state);
  const howl = howlMap.get(eid);
  if (howl && AudioEmitter.loop[eid] === 0) {
    AudioEmitter.playing[eid] = 0;
    prevPlaying.set(eid, 0);
    howl.stop();
    howl.play();
    AudioEmitter.playing[eid] = 1;
    prevPlaying.set(eid, 1);
    return;
  }
  AudioEmitter.playing[eid] = 1;
}

/** Retoma o AudioContext do Howler se estiver suspenso (política de autoplay dos browsers). */
export function resumeAudioContextIfSuspended(): void {
  const ctx = Howler.ctx;
  if (ctx?.state === 'suspended') {
    void ctx.resume();
  }
}

/**
 * No browser, regista um `pointerdown` único para retomar o contexto de áudio.
 * Sem efeito fora de ambiente DOM.
 */
export function resumeAudioContextOnFirstUserGesture(): void {
  if (typeof document === 'undefined') return;
  const handler = () => {
    resumeAudioContextIfSuspended();
    document.removeEventListener('pointerdown', handler);
  };
  document.addEventListener('pointerdown', handler, { once: true });
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
  last: true,
  after: [TransformHierarchySystem],
  update(state: State) {
    if (state.headless) return;

    const { howlMap, prevPlaying, howlPropSnapshot } = getOrCreateState(state);
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
          if (AudioEmitter.loop[eid] === 0) {
            howl.on('end', () => {
              if (!state.exists(eid)) return;
              if (AudioEmitter.loop[eid] === 1) return;
              if (AudioEmitter.playing[eid] !== 1) return;
              AudioEmitter.playing[eid] = 0;
              prevPlaying.set(eid, 0);
            });
          }
          howlMap.set(eid, howl);
          howlPropSnapshot.set(eid, {
            volume: AudioEmitter.volume[eid],
            loop: AudioEmitter.loop[eid] === 1,
            rate: AudioEmitter.pitch[eid],
          });
        }
        howl.play();
      }

      if (playing === 0 && wasPlaying === 1 && howl) {
        howl.stop();
      }

      if (howl) {
        const nextVol = AudioEmitter.volume[eid];
        const nextLoop = AudioEmitter.loop[eid] === 1;
        const nextRate = AudioEmitter.pitch[eid];
        const snap = howlPropSnapshot.get(eid);
        if (!snap || snap.volume !== nextVol) {
          howl.volume(nextVol);
        }
        if (!snap || snap.loop !== nextLoop) {
          howl.loop(nextLoop);
        }
        if (!snap || snap.rate !== nextRate) {
          howl.rate(nextRate);
        }
        howlPropSnapshot.set(eid, {
          volume: nextVol,
          loop: nextLoop,
          rate: nextRate,
        });

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
        howlPropSnapshot.delete(eid);
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
      // Howler.pos trata browsers só com setPosition() vs AudioParam positionX/Y/Z.
      Howler.pos(x, y, z);
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
