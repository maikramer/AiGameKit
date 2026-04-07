import { Box3 } from 'three';
import { defineQuery, type Adapter, type State, type System } from '../../core';
import { loadGltfAnimated } from '../../extras/gltf-bridge';
import { GltfAnimator } from '../../extras/gltf-animator';
import { animatorRegistry, registerAnimator } from '../gltf-anim/systems';
import { HasAnimator } from '../animation/components';
import { isKeyDown } from '../input/utils';
import { WorldTransform } from '../transforms';
import { Player, PlayerGltfConfig } from './components';

let nextModelUrlIndex = 1;
const modelUrlByIndex = new Map<number, string>();

const inFlightByState = new WeakMap<State, Set<number>>();
const yOffsetByState = new WeakMap<State, Map<number, number>>();

function assignPlayerGltfModelUrl(url: string): number {
  const idx = nextModelUrlIndex++;
  modelUrlByIndex.set(idx, url.trim());
  return idx;
}

export const playerGltfModelUrlAdapter: Adapter = (entity, value, _state) => {
  PlayerGltfConfig.modelUrlIndex[entity] = assignPlayerGltfModelUrl(value);
};

function getModelUrl(index: number): string | undefined {
  return modelUrlByIndex.get(index);
}

function isLoadInFlight(state: State, eid: number): boolean {
  return inFlightByState.get(state)?.has(eid) ?? false;
}

function setLoadInFlight(state: State, eid: number, v: boolean): void {
  let s = inFlightByState.get(state);
  if (!s) {
    s = new Set();
    inFlightByState.set(state, s);
  }
  if (v) {
    s.add(eid);
  } else {
    s.delete(eid);
  }
}

function getYOffset(state: State, eid: number): number {
  return yOffsetByState.get(state)?.get(eid) ?? 0;
}

function setYOffset(state: State, eid: number, y: number): void {
  let m = yOffsetByState.get(state);
  if (!m) {
    m = new Map();
    yOffsetByState.set(state, m);
  }
  m.set(eid, y);
}

const CLIP_IDLE = 'Animator3D_BreatheIdle';
const CLIP_WALK = 'Animator3D_Walk';
const CLIP_RUN = 'Animator3D_Run';

const MOVE_CODES = [
  'KeyW',
  'KeyA',
  'KeyS',
  'KeyD',
  'ArrowUp',
  'ArrowDown',
  'ArrowLeft',
  'ArrowRight',
] as const;

function isMovementPressed(): boolean {
  return MOVE_CODES.some((c) => isKeyDown(c));
}

function isRunModifier(): boolean {
  return isKeyDown('ShiftLeft') || isKeyDown('ShiftRight');
}

const playerGltfSetupQuery = defineQuery([Player, PlayerGltfConfig]);

/** Runs in the first setup bucket so {@link HasAnimator} exists before the procedural character is spawned. */
export const PlayerGltfEnsureHasAnimatorSystem: System = {
  group: 'setup',
  first: true,
  update: (state) => {
    for (const eid of playerGltfSetupQuery(state.world)) {
      if (!state.hasComponent(eid, HasAnimator)) {
        state.addComponent(eid, HasAnimator);
      }
    }
  },
};

export const PlayerGltfSetupSystem: System = {
  group: 'draw',
  update: (state) => {
    for (const eid of playerGltfSetupQuery(state.world)) {
      if (PlayerGltfConfig.loaded[eid] !== 0) {
        continue;
      }
      if (isLoadInFlight(state, eid)) {
        continue;
      }

      const urlIndex = PlayerGltfConfig.modelUrlIndex[eid];
      const url = urlIndex > 0 ? getModelUrl(urlIndex) : undefined;
      if (!url) {
        PlayerGltfConfig.loaded[eid] = 1;
        continue;
      }

      setLoadInFlight(state, eid, true);
      void loadGltfAnimated(state, url)
        .then((gltf) => {
          const box = new Box3().setFromObject(gltf.scene);
          const yOffset = Number.isFinite(box.min.y) ? -box.min.y : 0;
          setYOffset(state, eid, yOffset);

          const animator = new GltfAnimator(gltf, { crossfadeDuration: 0.25 });
          const regIdx = registerAnimator(animator);
          PlayerGltfConfig.animatorRegistryIndex[eid] = regIdx;
          animator.play(CLIP_IDLE);
        })
        .catch((err: unknown) => {
          console.error('[player-gltf] load failed', err);
        })
        .finally(() => {
          PlayerGltfConfig.loaded[eid] = 1;
          setLoadInFlight(state, eid, false);
        });
    }
  },
};

const playerGltfAnimQuery = defineQuery([Player, PlayerGltfConfig]);

export const PlayerGltfAnimStateSystem: System = {
  group: 'simulation',
  update: (state) => {
    const dt = state.time.deltaTime;

    for (const eid of playerGltfAnimQuery(state.world)) {
      if (PlayerGltfConfig.loaded[eid] !== 1) {
        continue;
      }
      const regIdx = PlayerGltfConfig.animatorRegistryIndex[eid];
      if (regIdx === 0) {
        continue;
      }

      const animator = animatorRegistry.get(regIdx);
      if (!animator) {
        continue;
      }

      const moving = isMovementPressed();
      const run = moving && isRunModifier();
      let wantClip = CLIP_IDLE;
      if (moving) {
        wantClip = run ? CLIP_RUN : CLIP_WALK;
      }

      if (animator.activeClipName !== wantClip) {
        animator.play(wantClip);
      }

      animator.update(dt);

      if (!state.hasComponent(eid, WorldTransform)) {
        continue;
      }

      const yOff = getYOffset(state, eid);
      const root = animator.root;
      root.position.set(
        WorldTransform.posX[eid],
        WorldTransform.posY[eid] + yOff,
        WorldTransform.posZ[eid]
      );
      root.quaternion.set(
        WorldTransform.rotX[eid],
        WorldTransform.rotY[eid],
        WorldTransform.rotZ[eid],
        WorldTransform.rotW[eid]
      );
    }
  },
};
