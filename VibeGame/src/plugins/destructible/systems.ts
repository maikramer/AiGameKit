import * as THREE from 'three';
import { defineQuery } from '../../core';
import type { State, System } from '../../core';
import { animatorRegistry } from '../gltf-anim/systems';
import { InputState } from '../input/components';
import { spawnFloatingText } from '../floating-text/utils';
import { spawnParticleBurst } from '../particles/utils';
import { presetName } from '../particles/presets';
import { Rigidbody } from '../physics/components';
import { getBodyForEntity } from '../physics/systems';
import { PlayerController, PlayerGltfConfig } from '../player/components';
import { Transform } from '../transforms/components';
import { Destructible } from './components';
import {
  deleteDestructiblePopupText,
  emitDestructibleDestroyed,
  getDestructiblePopupText,
} from './utils';

const SWING_COOLDOWN_SEC = 0.4;
const FALLBACK_IMPACT_DELAY = 0.5;

const destructibleQuery = defineQuery([Destructible, Transform]);
const playerQuery = defineQuery([PlayerController, InputState]);

const lastSwingAt = new WeakMap<State, number>();

const _color = new THREE.Color();

/** Time from swing start until the blow lands, from the attack clip length. */
function attackImpactDelay(player: number, fraction: number): number {
  const regIdx = PlayerGltfConfig.animatorRegistryIndex[player];
  const animator = regIdx ? animatorRegistry.get(regIdx) : undefined;
  if (!animator) return FALLBACK_IMPACT_DELAY;

  const attackName = animator.clipNames.find((name) =>
    name.toLowerCase().includes('attack')
  );
  const duration = attackName
    ? (animator.clips.get(attackName)?.duration ?? 0)
    : 0;
  return duration > 0 ? duration * fraction : FALLBACK_IMPACT_DELAY;
}

/** Snap the player's yaw toward the prop so the swing reads as aimed at it. */
function facePlayerToward(state: State, player: number, eid: number): void {
  const dx = Transform.posX[eid] - Transform.posX[player];
  const dz = Transform.posZ[eid] - Transform.posZ[player];
  const half = Math.atan2(dx, dz) * 0.5;
  const sinHalf = Math.sin(half);
  const cosHalf = Math.cos(half);
  const body = getBodyForEntity(state, player);
  if (body) body.setRotation({ x: 0, y: sinHalf, z: 0, w: cosHalf }, true);
  if (state.hasComponent(player, Rigidbody)) {
    Rigidbody.rotX[player] = 0;
    Rigidbody.rotY[player] = sinHalf;
    Rigidbody.rotZ[player] = 0;
    Rigidbody.rotW[player] = cosHalf;
  }
  Transform.rotX[player] = 0;
  Transform.rotY[player] = sinHalf;
  Transform.rotZ[player] = 0;
  Transform.rotW[player] = cosHalf;
  Transform.dirty[player] = 1;
}

function breakProp(state: State, eid: number): void {
  const x = Transform.posX[eid];
  const y = Transform.posY[eid];
  const z = Transform.posZ[eid];

  spawnParticleBurst(state, {
    x,
    y: y + 0.8,
    z,
    preset: presetName(Destructible.preset[eid]),
    count: Destructible.burstCount[eid] || 60,
  });

  const popup = getDestructiblePopupText(state, eid);
  if (popup) {
    _color.setRGB(
      Destructible.popupColorR[eid],
      Destructible.popupColorG[eid],
      Destructible.popupColorB[eid]
    );
    spawnFloatingText(state, popup, {
      x,
      y: y + 1.2,
      z,
      color: _color.getHex(),
      size: Destructible.popupSize[eid] || 0.4,
    });
  }

  emitDestructibleDestroyed(state, eid, x, y, z);
  deleteDestructiblePopupText(state, eid);
  state.destroyEntity(eid);
}

export const DestructibleSystem: System = {
  group: 'simulation',

  update(state: State) {
    const props = destructibleQuery(state.world);
    if (props.length === 0) return;

    const dt = state.time.deltaTime;
    const player = playerQuery(state.world)[0] ?? 0;

    // Land committed swings first.
    for (const eid of props) {
      const pending = Destructible.pendingImpact[eid];
      if (pending <= 0) continue;
      const left = pending - dt;
      if (left > 0) {
        Destructible.pendingImpact[eid] = left;
        continue;
      }
      Destructible.pendingImpact[eid] = 0;
      if (Destructible.hitsTaken[eid] >= (Destructible.hits[eid] || 1)) {
        breakProp(state, eid);
      } else if (Destructible.sparkOnHit[eid]) {
        spawnParticleBurst(state, {
          x: Transform.posX[eid],
          y: Transform.posY[eid] + 0.8,
          z: Transform.posZ[eid],
          preset: 'sparks',
          count: 15,
          duration: 0.3,
        });
      }
    }

    if (!player || InputState.primaryAction[player] !== 1) return;

    const now = state.time.elapsed;
    const last = lastSwingAt.get(state) ?? -Infinity;
    if (now - last < SWING_COOLDOWN_SEC) return;

    // Commit the swing to the nearest destructible within its own range.
    let target = 0;
    let bestDistSq = Infinity;
    for (const eid of props) {
      if (Destructible.pendingImpact[eid] > 0) continue;
      const dx = Transform.posX[player] - Transform.posX[eid];
      const dz = Transform.posZ[player] - Transform.posZ[eid];
      const distSq = dx * dx + dz * dz;
      const range = Destructible.range[eid] || 3.5;
      if (distSq < range * range && distSq < bestDistSq) {
        bestDistSq = distSq;
        target = eid;
      }
    }
    if (!target) return;

    lastSwingAt.set(state, now);
    if (Destructible.faceOnHit[target]) {
      facePlayerToward(state, player, target);
    }
    Destructible.hitsTaken[target] += 1;
    Destructible.pendingImpact[target] = attackImpactDelay(
      player,
      Destructible.impactFraction[target] || 0.75
    );
  },
};
