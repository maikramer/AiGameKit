import * as THREE from 'three';
import { loadGltfToSceneWithAnimator, playAudioEmitter } from 'vibegame';
import type { GltfAnimator, MonoBehaviourContext, State } from 'vibegame';
import {
  Transform,
  defineQuery,
  PlayerController,
  MonoBehaviour,
} from 'vibegame';
import { getTerrainHeightAt } from '../../../../src/plugins/terrain/systems.ts';
import { castRapierRay } from '../../../../src/plugins/raycast/utils.ts';
import {
  Health,
  damageHealth,
  isDead,
} from '../../../../src/plugins/combat/components.ts';
import { spawnFloatingText } from '../../../../src/plugins/floating-text/utils.ts';
import { spawnParticleBurst } from '../../../../src/plugins/particles/utils.ts';

const MODEL_URL = '/assets/meshes/boss_ogre_rigged_animated.glb';
const IDLE_CLIP = 'Animator3D_BreatheIdle';
const WALK_CLIP = 'Animator3D_Walk';
const ATTACK_CLIP = 'Animator3D_Attack';
const ROAR_CLIP = 'Animator3D_Roar';

const DETECT_RANGE = 40;
const ATTACK_RANGE = 4.0;
const ATTACK_DAMAGE = 25;
const ATTACK_COOLDOWN = 1.5;
const BOSS_HP = 300;
const CHASE_SPEED = 2.8;
const TURN_RATE = 2.0;
const ACCEL = 3.0;
const WATER_LEVEL = 1.25;
const ROAR_DURATION = 2.5;
const DEATH_DISABLE_DELAY = 5.0;

let eidSfxBossRoar = -1;

function resolveBossSfx(state: State): void {
  if (eidSfxBossRoar >= 0) return;
  eidSfxBossRoar = state.getEntityByName('sfx-boss-roar') ?? -1;
}

type CombatState = 'idle' | 'seek' | 'attack' | 'dead';

interface BossState {
  heading: number;
  targetHeading: number;
  speed: number;
  footOffset: number;
  ready: boolean;
  group: THREE.Group | null;
  animator: GltfAnimator | null;
  playing: string;
  combatState: CombatState;
  attackTimer: number;
  deathTimer: number;
  deathHandled: boolean;
  hasRoared: boolean;
  roarTimer: number;
}

const state = new Map<number, BossState>();
const playerQuery = defineQuery([PlayerController]);
const _box = new THREE.Box3();

let cachedPlayerEid = 0;

function findPlayer(ctx: MonoBehaviourContext): number {
  if (cachedPlayerEid && Health.current[cachedPlayerEid] > 0)
    return cachedPlayerEid;
  const players = playerQuery(ctx.state.world);
  cachedPlayerEid = players[0] ?? 0;
  return cachedPlayerEid;
}

function wrapAngle(a: number): number {
  return Math.atan2(Math.sin(a), Math.cos(a));
}

const _downDir = { x: 0, y: -1, z: 0 };

function groundHeight(
  ctx: MonoBehaviourContext,
  x: number,
  z: number,
  fromY: number
): number {
  const origin = { x, y: fromY + 60, z };
  const hit = castRapierRay(ctx.state, origin, _downDir, 2000, 0xffff);
  if (hit) return hit.point.y;
  const hm = getTerrainHeightAt(ctx.state, x, z);
  if (Number.isFinite(hm)) return hm;
  return 0;
}

const FOOT_RADIUS = 0.4;

function footprintHeight(
  ctx: MonoBehaviourContext,
  x: number,
  z: number,
  fromY: number
): number {
  let best = groundHeight(ctx, x, z, fromY);
  if (!Number.isFinite(best)) return best;
  for (const [ox, oz] of [
    [FOOT_RADIUS, 0],
    [-FOOT_RADIUS, 0],
    [0, FOOT_RADIUS],
    [0, -FOOT_RADIUS],
  ]) {
    const h = groundHeight(ctx, x + ox, z + oz, fromY);
    if (Number.isFinite(h) && h > best) best = h;
  }
  return best;
}

export function start(ctx: MonoBehaviourContext): void {
  const eid = ctx.entity;
  const b: BossState = {
    heading: 0,
    targetHeading: 0,
    speed: 0,
    footOffset: 0,
    ready: false,
    group: null,
    animator: null,
    playing: '',
    combatState: 'idle',
    attackTimer: 0,
    deathTimer: 0,
    deathHandled: false,
    hasRoared: false,
    roarTimer: 0,
  };
  state.set(eid, b);

  if (!ctx.state.hasComponent(eid, Health)) {
    ctx.state.addComponent(eid, Health);
  }
  Health.current[eid] = BOSS_HP;
  Health.max[eid] = BOSS_HP;

  void loadGltfToSceneWithAnimator(ctx.state, MODEL_URL, {
    crossfadeDuration: 0.25,
  }).then((result) => {
    if (state.get(eid) !== b) {
      result.group.removeFromParent();
      return;
    }
    b.group = result.group;
    b.animator = result.animator;
    b.group.updateWorldMatrix(true, true);
    _box.setFromObject(b.group);
    b.footOffset = Number.isFinite(_box.min.y) ? -_box.min.y : 0;
  });
}

export function onDestroy(ctx: MonoBehaviourContext): void {
  const b = state.get(ctx.entity);
  b?.group?.removeFromParent();
  state.delete(ctx.entity);
}

function handleDeath(
  ctx: MonoBehaviourContext,
  b: BossState,
  eid: number
): void {
  if (b.deathHandled) return;
  b.deathHandled = true;
  b.combatState = 'dead';
  b.deathTimer = DEATH_DISABLE_DELAY;
  b.speed = 0;

  const x = Transform.posX[eid];
  const y = Transform.posY[eid];
  const z = Transform.posZ[eid];

  spawnFloatingText(ctx.state, 'BOSS DEFEATED!', {
    x,
    y: y + 3.0,
    z,
    color: 0xffd700,
    size: 1.0,
    duration: 3.0,
  });

  spawnParticleBurst(ctx.state, {
    x,
    y: y + 1.0,
    z,
    preset: 'explosion',
    count: 30,
    duration: 1.2,
  });
}

export function update(ctx: MonoBehaviourContext): void {
  const eid = ctx.entity;
  const b = state.get(eid);
  if (!b || !b.group) return;

  resolveBossSfx(ctx.state);
  const dt = ctx.deltaTime;

  if (b.combatState === 'dead') {
    b.deathTimer -= dt;
    if (b.deathTimer <= 0) {
      MonoBehaviour.enabled[eid] = 0;
    }
    return;
  }

  if (isDead(eid)) {
    handleDeath(ctx, b, eid);
    return;
  }

  b.animator?.update(dt);

  const x = Transform.posX[eid];
  const z = Transform.posZ[eid];

  if (!b.ready) {
    const gy = groundHeight(ctx, x, z, 500);
    if (!Number.isFinite(gy) || gy === 0) return;
    b.ready = true;
  }

  const playerEid = findPlayer(ctx);

  if (playerEid > 0 && Health.max[playerEid] > 0) {
    const px = Transform.posX[playerEid];
    const pz = Transform.posZ[playerEid];
    const dx = px - x;
    const dz = pz - z;
    const distSq = dx * dx + dz * dz;

    if (distSq <= ATTACK_RANGE * ATTACK_RANGE) {
      b.combatState = 'attack';
      b.targetHeading = Math.atan2(dx, dz);
      b.attackTimer -= dt;
      if (b.attackTimer <= 0) {
        damageHealth(playerEid, ATTACK_DAMAGE);
        b.attackTimer = ATTACK_COOLDOWN;
      }
    } else if (distSq <= DETECT_RANGE * DETECT_RANGE) {
      b.combatState = 'seek';
      b.targetHeading = Math.atan2(dx, dz);
      if (!b.hasRoared) {
        b.hasRoared = true;
        b.roarTimer = ROAR_DURATION;
        if (eidSfxBossRoar >= 0) playAudioEmitter(ctx.state, eidSfxBossRoar);
      }
    } else {
      b.combatState = 'idle';
    }
  } else {
    b.combatState = 'idle';
  }

  const roaring = b.combatState === 'seek' && b.roarTimer > 0;
  if (roaring) {
    b.roarTimer -= dt;
    b.attackTimer = ATTACK_COOLDOWN;
  }

  const err = wrapAngle(b.targetHeading - b.heading);
  const maxTurn = TURN_RATE * dt;
  b.heading = wrapAngle(b.heading + Math.min(maxTurn, Math.max(-maxTurn, err)));

  const targetSpeed = b.combatState === 'seek' && !roaring ? CHASE_SPEED : 0;
  if (b.speed < targetSpeed)
    b.speed = Math.min(targetSpeed, b.speed + ACCEL * dt);
  else if (b.speed > targetSpeed)
    b.speed = Math.max(targetSpeed, b.speed - ACCEL * dt);

  let nx = x;
  let nz = z;
  if (b.speed > 0.001) {
    nx = x + Math.sin(b.heading) * b.speed * dt;
    nz = z + Math.cos(b.heading) * b.speed * dt;
    const aheadY = groundHeight(ctx, nx, nz, Transform.posY[eid]);
    if (!Number.isFinite(aheadY) || aheadY < WATER_LEVEL) {
      b.targetHeading = wrapAngle(b.heading + Math.PI);
      nx = x;
      nz = z;
    }
  }

  const groundY = footprintHeight(ctx, nx, nz, Transform.posY[eid]);
  if (!Number.isFinite(groundY)) return;

  let clip: string;
  if (b.combatState === 'attack') {
    clip = ATTACK_CLIP;
  } else if (roaring) {
    clip = ROAR_CLIP;
  } else if (b.combatState === 'seek') {
    clip = WALK_CLIP;
  } else {
    clip = IDLE_CLIP;
  }
  if (b.animator && b.playing !== clip) {
    if (clip === ROAR_CLIP) {
      b.animator.play(ROAR_CLIP, { loop: false });
    } else {
      b.animator.play(clip);
    }
    b.playing = clip;
  }

  Transform.posX[eid] = nx;
  Transform.posY[eid] = groundY + b.footOffset;
  Transform.posZ[eid] = nz;
  Transform.eulerY[eid] = b.heading * (180 / Math.PI);
  Transform.dirty[eid] = 1;

  b.group.position.set(nx, groundY + b.footOffset, nz);
  b.group.rotation.set(0, b.heading, 0);
}
