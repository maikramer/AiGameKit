import { defineQuery, animatorRegistry } from 'vibegame';
import type { MonoBehaviourContext } from 'vibegame';
import {
  Transform,
  WorldTransform,
  PlayerController,
  PlayerGltfConfig,
  ParticleEmitter,
  InputState,
} from 'vibegame';
import { Rigidbody } from '../../../../src/plugins/physics/components';
import { getBodyForEntity } from '../../../../src/plugins/physics/systems';
import { addStone } from './inventory';

const ATTACK_RANGE = 3.5;
const COOLDOWN_SEC = 0.4;
// Explode near the end of the swing, not on the key press: the burst is
// scheduled at this fraction of the hero's attack clip duration.
const IMPACT_FRACTION = 0.75;
const FALLBACK_IMPACT_DELAY = 0.5;

const playerQuery = defineQuery([PlayerController]);

let cachedPlayer = 0;
let cooldownRemaining = 0;
// Per-rock countdown (seconds) between the swing starting and the burst.
const pendingImpact = new Map<number, number>();

function findPlayer(ctx: MonoBehaviourContext): number {
  if (cachedPlayer && Transform.posX[cachedPlayer] !== undefined)
    return cachedPlayer;
  cachedPlayer = playerQuery(ctx.state.world)[0] ?? 0;
  return cachedPlayer;
}

/** Time from swing start until the blow "lands", from the attack clip length. */
function attackImpactDelay(player: number): number {
  const regIdx = PlayerGltfConfig.animatorRegistryIndex[player];
  const animator = regIdx ? animatorRegistry.get(regIdx) : undefined;
  if (!animator) return FALLBACK_IMPACT_DELAY;

  const attackName = animator.clipNames.find((name) =>
    name.toLowerCase().includes('attack')
  );
  const duration = attackName
    ? (animator.clips.get(attackName)?.duration ?? 0)
    : 0;
  return duration > 0 ? duration * IMPACT_FRACTION : FALLBACK_IMPACT_DELAY;
}

function explode(ctx: MonoBehaviourContext, eid: number): void {
  const rockX = Transform.posX[eid];
  const rockY = Transform.posY[eid];
  const rockZ = Transform.posZ[eid];

  const explosion = ctx.state.createEntity();
  // addComponent zeroes every field (scale 0, rotW 0) — restore a sane
  // identity or the emitter's world matrix degenerates.
  ctx.state.addComponent(explosion, Transform);
  Transform.posX[explosion] = rockX;
  Transform.posY[explosion] = rockY + 0.8;
  Transform.posZ[explosion] = rockZ;
  Transform.scaleX[explosion] = 1;
  Transform.scaleY[explosion] = 1;
  Transform.scaleZ[explosion] = 1;
  Transform.rotW[explosion] = 1;
  Transform.dirty[explosion] = 1;

  // The particle system reads WorldTransform; seed it directly so the
  // time-0 burst fires at the rock, not at the origin while the
  // hierarchy system catches up.
  ctx.state.addComponent(explosion, WorldTransform);
  WorldTransform.posX[explosion] = rockX;
  WorldTransform.posY[explosion] = rockY + 0.8;
  WorldTransform.posZ[explosion] = rockZ;
  WorldTransform.scaleX[explosion] = 1;
  WorldTransform.scaleY[explosion] = 1;
  WorldTransform.scaleZ[explosion] = 1;
  WorldTransform.rotW[explosion] = 1;

  ctx.state.addComponent(explosion, ParticleEmitter);
  ParticleEmitter.active[explosion] = 1;
  ParticleEmitter.preset[explosion] = 5; // explosion
  ParticleEmitter.burst[explosion] = 1;
  ParticleEmitter.looping[explosion] = 0;
  ParticleEmitter.burstCount[explosion] = 60;
  ParticleEmitter.duration[explosion] = 0.5;
  ParticleEmitter.worldSpace[explosion] = 0;

  addStone(1, rockX, rockY + 0.8, rockZ);
  pendingImpact.delete(eid);
  ctx.state.destroyEntity(eid);
}

export function start(_ctx: MonoBehaviourContext): void {}

export function update(ctx: MonoBehaviourContext): void {
  const eid = ctx.entity;
  const dt = ctx.deltaTime;

  if (cooldownRemaining > 0) {
    cooldownRemaining -= dt;
  }

  // A swing already committed to this rock: wait for the blow to land.
  const countdown = pendingImpact.get(eid);
  if (countdown !== undefined) {
    const left = countdown - dt;
    if (left <= 0) {
      explode(ctx, eid);
    } else {
      pendingImpact.set(eid, left);
    }
    return;
  }

  const player = findPlayer(ctx);
  if (!player) return;

  const dx = Transform.posX[player] - Transform.posX[eid];
  const dz = Transform.posZ[player] - Transform.posZ[eid];
  const distSq = dx * dx + dz * dz;

  // primaryAction is the buffered attack input (left click or J via
  // addInputMapping in main.ts) — the same edge that plays the hero's
  // attack clip, so the smash stays in sync with the swing.
  if (
    distSq < ATTACK_RANGE * ATTACK_RANGE &&
    InputState.primaryAction[player] === 1 &&
    cooldownRemaining <= 0
  ) {
    const toRockX = Transform.posX[eid] - Transform.posX[player];
    const toRockZ = Transform.posZ[eid] - Transform.posZ[player];
    const angle = Math.atan2(toRockX, toRockZ);
    const half = angle * 0.5;
    const sinHalf = Math.sin(half);
    const cosHalf = Math.cos(half);
    const body = getBodyForEntity(ctx.state, player);
    if (body) body.setRotation({ x: 0, y: sinHalf, z: 0, w: cosHalf }, true);
    Rigidbody.rotX[player] = 0;
    Rigidbody.rotY[player] = sinHalf;
    Rigidbody.rotZ[player] = 0;
    Rigidbody.rotW[player] = cosHalf;
    Transform.rotX[player] = 0;
    Transform.rotY[player] = sinHalf;
    Transform.rotZ[player] = 0;
    Transform.rotW[player] = cosHalf;
    Transform.dirty[player] = 1;

    cooldownRemaining = COOLDOWN_SEC;
    pendingImpact.set(eid, attackImpactDelay(player));
  }
}
