import { logger } from '../../core/utils/logger';
import { Howl, Howler } from 'howler';
import { defineQuery } from '../../core';
import type { State, System } from '../../core';
import { AudioSource, AudioListener } from './components';
import { MainCamera } from '../rendering/components';
import { TransformHierarchySystem } from '../transforms/systems';
import { Transform, WorldTransform } from '../transforms/components';
import { GltfAnimationState } from '../gltf-anim/components';
import { animatorRegistry } from '../gltf-anim/systems';
import {
  fireClipMarkers,
  getClipSounds,
  getFollowingPlays,
  pruneFollowingPlays,
} from './bank';

// Howler.js spatial audio uses stereo panning only (no HRTF).

const clipRegistry = new Map<number, string>();

export function registerAudioClip(id: number, url: string): void {
  clipRegistry.set(id, url);
}

/** Limpa o registo global de clips (útil em testes para evitar fuga entre ficheiros). */
export function clearAudioClipRegistry(): void {
  clipRegistry.clear();
  clipSoundTracker.clear();
  clipSoundCleanupRegistered.clear();
}

const audioQuery = defineQuery([AudioSource]);
const listenerQuery = defineQuery([AudioListener, MainCamera, WorldTransform]);
const mainCameraTransformQuery = defineQuery([MainCamera, Transform]);
const animClipQuery = defineQuery([GltfAnimationState]);

/** Últimos valores aplicados ao Howl (evitar setters por frame: Howler reinicia reprodução em `loop(true)` com som ativo; `rate()` também altera timers). */
interface HowlPropSnapshot {
  volume: number;
  loop: boolean;
  rate: number;
  // Undefined for non-spatial sources; mirrored from the Howl ctor so the
  // per-frame pannerAttr() call is skipped when the values are unchanged.
  pannerRefDistance?: number;
  pannerMaxDistance?: number;
  pannerRolloff?: number;
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
  if (howl && AudioSource.loop[eid] === 0) {
    AudioSource.playing[eid] = 0;
    prevPlaying.set(eid, 0);
    howl.stop();
    howl.play();
    AudioSource.playing[eid] = 1;
    prevPlaying.set(eid, 1);
    return;
  }
  AudioSource.playing[eid] = 1;
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
      const clipId = AudioSource.clipPath[eid];
      const playing = AudioSource.playing[eid];
      const wasPlaying = prevPlaying.get(eid) ?? 0;

      let howl = howlMap.get(eid);

      if (playing === 1 && wasPlaying === 0) {
        if (!howl) {
          const url = clipRegistry.get(clipId);
          if (!url) continue;

          const spatial = AudioSource.spatial[eid] === 1;
          howl = new Howl({
            src: [url],
            preload: true,
            loop: AudioSource.loop[eid] === 1,
            volume: AudioSource.volume[eid],
            rate: AudioSource.pitch[eid],
            ...(spatial && {
              pos: [
                Transform.posX[eid],
                Transform.posY[eid],
                Transform.posZ[eid],
              ],
              pannerAttr: {
                refDistance: AudioSource.minDistance[eid],
                maxDistance: AudioSource.maxDistance[eid],
                rolloffFactor: AudioSource.rolloff[eid],
              },
            }),
          });
          if (AudioSource.loop[eid] === 0) {
            howl.on('end', () => {
              if (!state.exists(eid)) return;
              if (AudioSource.loop[eid] === 1) return;
              if (AudioSource.playing[eid] !== 1) return;
              AudioSource.playing[eid] = 0;
              prevPlaying.set(eid, 0);
            });
          }
          howlMap.set(eid, howl);
          howlPropSnapshot.set(eid, {
            volume: AudioSource.volume[eid],
            loop: AudioSource.loop[eid] === 1,
            rate: AudioSource.pitch[eid],
            pannerRefDistance: spatial
              ? AudioSource.minDistance[eid]
              : undefined,
            pannerMaxDistance: spatial
              ? AudioSource.maxDistance[eid]
              : undefined,
            pannerRolloff: spatial ? AudioSource.rolloff[eid] : undefined,
          });
        }
        howl.play();
      }

      if (playing === 0 && wasPlaying === 1 && howl) {
        howl.stop();
      }

      if (howl) {
        const nextVol = AudioSource.volume[eid];
        const nextLoop = AudioSource.loop[eid] === 1;
        const nextRate = AudioSource.pitch[eid];
        const spatial = AudioSource.spatial[eid] === 1;

        // Mutate the cached snapshot in place instead of allocating a new
        // object every frame. The dirty-gating still guards the Howl setters.
        let snap = howlPropSnapshot.get(eid);
        if (!snap) {
          snap = { volume: nextVol, loop: nextLoop, rate: nextRate };
          howlPropSnapshot.set(eid, snap);
        }

        if (snap.volume !== nextVol) {
          howl.volume(nextVol);
          snap.volume = nextVol;
        }
        if (snap.loop !== nextLoop) {
          howl.loop(nextLoop);
          snap.loop = nextLoop;
        }
        if (snap.rate !== nextRate) {
          howl.rate(nextRate);
          snap.rate = nextRate;
        }

        if (spatial) {
          howl.pos(
            Transform.posX[eid],
            Transform.posY[eid],
            Transform.posZ[eid]
          );
          const nextRef = AudioSource.minDistance[eid];
          const nextMax = AudioSource.maxDistance[eid];
          const nextRoll = AudioSource.rolloff[eid];
          if (
            snap.pannerRefDistance !== nextRef ||
            snap.pannerMaxDistance !== nextMax ||
            snap.pannerRolloff !== nextRoll
          ) {
            snap.pannerRefDistance = nextRef;
            snap.pannerMaxDistance = nextMax;
            snap.pannerRolloff = nextRoll;
            howl.pannerAttr({
              refDistance: nextRef,
              maxDistance: nextMax,
              rolloffFactor: nextRoll,
            });
          }
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
      logger.warn(
        '[audio] Nenhuma entidade com AudioListener + MainCamera + WorldTransform; áudio espacial pode falhar.'
      );
    }
  },
  dispose(state: State) {
    const audio = AUDIO_STATE.get(state);
    if (audio) {
      for (const howl of audio.howlMap.values()) {
        try {
          howl.unload();
        } catch {
          // Howl may already be unloaded.
        }
      }
      audio.howlMap.clear();
      audio.prevPlaying.clear();
      audio.howlPropSnapshot.clear();
      AUDIO_STATE.delete(state);
    }
    // Stops all Howler sounds and frees the shared AudioContext.
    try {
      Howler.unload();
    } catch {
      // No AudioContext to free.
    }
    clearAudioClipRegistry();
  },
};

/** Per-entity normalized clip time last frame, for marker crossing detection. */
const clipSoundTracker = new Map<number, { clip: string; norm: number }>();
/** Eids with a registered destroy-cleanup, so we don't stack callbacks. */
const clipSoundCleanupRegistered = new Set<number>();

/**
 * Reposiciona sons espaciais ligados a entidades (`playSoundOn`) e dispara sons
 * fixados a animações (`addClipSound`) quando o tempo normalizado do clip cruza
 * o marcador. Corre depois do `GltfAnimationUpdateSystem` (grupo 'draw').
 */
export const SoundBankSystem: System = {
  group: 'draw',
  update(state: State) {
    if (state.headless) return;

    // Sons que seguem entidades: actualiza posição ou solta se a entidade morreu.
    pruneFollowingPlays((eid) => state.exists(eid));
    for (const play of getFollowingPlays()) {
      const eid = play.followEid;
      if (!state.exists(eid)) continue;
      const t = state.hasComponent(eid, WorldTransform)
        ? WorldTransform
        : Transform;
      play.setPos(t.posX[eid], t.posY[eid], t.posZ[eid]);
    }

    // Sons fixados a clips de animação.
    for (const eid of animClipQuery(state.world)) {
      const idx = GltfAnimationState.registryIndex[eid];
      if (idx === 0) continue;
      const animator = animatorRegistry.get(idx);
      if (!animator) continue;

      const clip = animator.activeClipName;
      if (!clip || !getClipSounds(clip)) {
        clipSoundTracker.delete(eid);
        continue;
      }
      const norm = animator.currentNormalizedTime;
      const prev = clipSoundTracker.get(eid);
      if (!prev || prev.clip !== clip) {
        // Início do clip: dispara marcadores em at=0 a partir de -epsilon.
        fireClipMarkers(eid, clip, -1, norm);
      } else {
        fireClipMarkers(eid, clip, prev.norm, norm);
      }
      if (prev) {
        prev.clip = clip;
        prev.norm = norm;
      } else {
        clipSoundTracker.set(eid, { clip, norm });
      }
      if (!clipSoundCleanupRegistered.has(eid)) {
        clipSoundCleanupRegistered.add(eid);
        // Recycle-safe: drop tracker state on destroy (not an exists() sweep,
        // which a reused eid would survive with stale norm).
        state.onDestroy(eid, () => {
          clipSoundTracker.delete(eid);
          clipSoundCleanupRegistered.delete(eid);
        });
      }
    }
  },
};
