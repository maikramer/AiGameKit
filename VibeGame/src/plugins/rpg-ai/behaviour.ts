import { defineQuery } from '../../core';
import type { State } from '../../core';
import { Transform } from '../transforms/components';
import {
  FactionComponent,
  Health,
  damageHealth,
  isHostile,
} from '../combat/components';
import { setAgentTarget, isNavMeshReady } from '../navmesh';
import { NavMeshAgent } from '../navmesh/components';
import {
  AI_MODE_ATTACK,
  AI_MODE_CHASE,
  AI_MODE_DEAD,
  AI_MODE_DETECT,
  AI_MODE_IDLE,
  AI_MODE_LUNGE,
  AiStateComponent,
  type AiInstanceState,
  type MeleeAiConfig,
} from './components';

const hostilesQuery = defineQuery([Health, FactionComponent]);

const DETECT_GRACE = 0;
const LUNGE_HIT_FACTOR = 1.5;
const LUNGE_BURST_SPEED = 6.0;

function distanceXZ(ax: number, az: number, bx: number, bz: number): number {
  const dx = ax - bx;
  const dz = az - bz;
  return Math.sqrt(dx * dx + dz * dz);
}

function entityAlive(state: State, eid: number): boolean {
  if (eid <= 0) return false;
  if (typeof state.exists === 'function' && !state.exists(eid)) return false;
  return Health.current[eid] > 0;
}

/**
 * Find the nearest hostile target for `eid`. Uses an explicit `config.targetEid`
 * when set; otherwise scans entities with Health + FactionComponent and picks
 * the nearest one that is hostile (via `isHostile`) and alive.
 */
export function acquireTarget(
  state: State,
  eid: number,
  config: MeleeAiConfig
): number {
  const explicit = config.targetEid;
  if (explicit !== undefined && explicit > 0 && entityAlive(state, explicit)) {
    return explicit;
  }
  const ox = Transform.posX[eid];
  const oz = Transform.posZ[eid];
  let bestEid = 0;
  let bestDist = Infinity;
  for (const candidate of hostilesQuery(state.world)) {
    if (candidate === eid) continue;
    if (!entityAlive(state, candidate)) continue;
    if (!isHostile(state, eid, candidate)) continue;
    const d = distanceXZ(
      ox,
      oz,
      Transform.posX[candidate],
      Transform.posZ[candidate]
    );
    if (d < bestDist) {
      bestDist = d;
      bestEid = candidate;
    }
  }
  return bestEid;
}

function withinLeash(
  inst: AiInstanceState,
  targetX: number,
  targetZ: number,
  leashRadius: number
): boolean {
  const dx = targetX - inst.originX;
  const dz = targetZ - inst.originZ;
  return dx * dx + dz * dz <= leashRadius * leashRadius;
}

function moveToward(
  state: State,
  eid: number,
  tx: number,
  tz: number,
  speed: number,
  dt: number
): void {
  if (isNavMeshReady() && NavMeshAgent.agentIndex[eid] !== -1) {
    setAgentTarget(state, eid, tx, Transform.posY[eid], tz);
    return;
  }
  const x = Transform.posX[eid];
  const z = Transform.posZ[eid];
  const dx = tx - x;
  const dz = tz - z;
  const dist = Math.sqrt(dx * dx + dz * dz);
  if (dist < 1e-4) return;
  const step = Math.min(dist, speed * dt);
  Transform.posX[eid] = x + (dx / dist) * step;
  Transform.posZ[eid] = z + (dz / dist) * step;
  Transform.dirty[eid] = 1;
}

function applyLungeMovement(
  eid: number,
  inst: AiInstanceState,
  config: MeleeAiConfig,
  targetEid: number,
  dt: number
): void {
  const x = Transform.posX[eid];
  const z = Transform.posZ[eid];
  let nx = x + inst.lungeDirX * LUNGE_BURST_SPEED * dt;
  let nz = z + inst.lungeDirZ * LUNGE_BURST_SPEED * dt;
  if (targetEid > 0) {
    const pdx = nx - Transform.posX[targetEid];
    const pdz = nz - Transform.posZ[targetEid];
    const pd = Math.sqrt(pdx * pdx + pdz * pdz);
    if (pd < config.lungeStandoff) {
      const ux = pd > 1e-3 ? pdx / pd : -inst.lungeDirX;
      const uz = pd > 1e-3 ? pdz / pd : -inst.lungeDirZ;
      nx = Transform.posX[targetEid] + ux * config.lungeStandoff;
      nz = Transform.posZ[targetEid] + uz * config.lungeStandoff;
    }
  }
  Transform.posX[eid] = nx;
  Transform.posZ[eid] = nz;
  Transform.dirty[eid] = 1;
}

/**
 * Advance the melee AI FSM one frame for `eid`. Reads/writes {@link Transform},
 * {@link AiStateComponent} and {@link AiInstanceState}; applies damage via the
 * engine `damageHealth` helper (which emits `combat:damaged`). Timing uses
 * `state.time.deltaTime` exclusively.
 */
export function runMeleeAiFrame(
  state: State,
  eid: number,
  config: MeleeAiConfig,
  inst: AiInstanceState
): void {
  const dt: number = state.time.deltaTime;
  const comp = AiStateComponent;

  if (comp.mode[eid] === AI_MODE_DEAD) return;

  if (!inst.originSet) {
    inst.originX = Transform.posX[eid];
    inst.originZ = Transform.posZ[eid];
    inst.originSet = true;
    comp.leash[eid] = config.leashRadius;
  }

  if (entityDead(eid)) {
    comp.mode[eid] = AI_MODE_DEAD;
    comp.target[eid] = 0;
    return;
  }

  const currentTarget = comp.target[eid];
  const targetEid =
    currentTarget > 0 && entityAlive(state, currentTarget)
      ? currentTarget
      : acquireTarget(state, eid, config);
  comp.target[eid] = targetEid;

  const mode = comp.mode[eid];

  if (targetEid <= 0) {
    tickIdle(state, eid, inst, config, dt);
    comp.mode[eid] = AI_MODE_IDLE;
    return;
  }

  const tx = Transform.posX[targetEid];
  const tz = Transform.posZ[targetEid];
  const dist = distanceXZ(Transform.posX[eid], Transform.posZ[eid], tx, tz);

  if (!withinLeash(inst, tx, tz, config.leashRadius)) {
    comp.mode[eid] = AI_MODE_IDLE;
    comp.target[eid] = 0;
    tickIdle(state, eid, inst, config, dt);
    return;
  }

  if (dist <= config.attackRange) {
    if (mode === AI_MODE_DETECT) {
      comp.mode[eid] = AI_MODE_CHASE;
    }
    tickAttack(eid, inst, config, targetEid, dist, dt);
    return;
  }

  if (dist <= config.detectRange) {
    if (mode === AI_MODE_IDLE || mode === AI_MODE_DETECT) {
      if (mode === AI_MODE_IDLE) {
        comp.mode[eid] = AI_MODE_DETECT;
        inst.detectTimer = DETECT_GRACE;
      } else {
        inst.detectTimer -= dt;
        if (inst.detectTimer <= 0) {
          comp.mode[eid] = AI_MODE_CHASE;
        }
      }
    }
    if (comp.mode[eid] !== AI_MODE_CHASE) {
      return;
    }
    tickChase(state, eid, config, targetEid, tx, tz, dt);
    return;
  }

  comp.mode[eid] = AI_MODE_IDLE;
  comp.target[eid] = 0;
  tickIdle(state, eid, inst, config, dt);
}

function entityDead(eid: number): boolean {
  return Health.current[eid] <= 0;
}

function tickChase(
  state: State,
  eid: number,
  config: MeleeAiConfig,
  targetEid: number,
  tx: number,
  tz: number,
  dt: number
): void {
  void targetEid;
  moveToward(state, eid, tx, tz, config.chaseSpeed, dt);
}

function tickAttack(
  eid: number,
  inst: AiInstanceState,
  config: MeleeAiConfig,
  targetEid: number,
  dist: number,
  dt: number
): void {
  const comp = AiStateComponent;

  if (inst.lungePhase === 'ready') {
    comp.mode[eid] = AI_MODE_ATTACK;
    comp.cooldown[eid] = Math.max(0, comp.cooldown[eid] - dt);
    if (comp.cooldown[eid] <= 0) {
      inst.lungePhase = 'windup';
      inst.lungeTimer = config.lungeWindup;
      const len = dist > 1e-3 ? dist : 1;
      const dx = Transform.posX[targetEid] - Transform.posX[eid];
      const dz = Transform.posZ[targetEid] - Transform.posZ[eid];
      inst.lungeDirX = dx / len;
      inst.lungeDirZ = dz / len;
    }
    return;
  }

  comp.mode[eid] = AI_MODE_LUNGE;

  if (inst.lungePhase === 'windup') {
    inst.lungeTimer -= dt;
    if (inst.lungeTimer <= 0) {
      inst.lungePhase = 'lunge';
      inst.lungeTimer = config.lungeDuration;
    }
    return;
  }

  if (inst.lungePhase === 'lunge') {
    inst.lungeTimer -= dt;
    applyLungeMovement(eid, inst, config, targetEid, dt);
    if (inst.lungeTimer <= 0) {
      const hitRange = config.attackRange * LUNGE_HIT_FACTOR;
      const dx = Transform.posX[targetEid] - Transform.posX[eid];
      const dz = Transform.posZ[targetEid] - Transform.posZ[eid];
      if (dx * dx + dz * dz <= hitRange * hitRange) {
        damageHealth(targetEid, config.attackDamage);
      }
      inst.lungePhase = 'recovery';
      inst.lungeTimer = config.lungeRecovery;
    }
    return;
  }

  inst.lungeTimer -= dt;
  if (inst.lungeTimer <= 0) {
    inst.lungePhase = 'ready';
    comp.cooldown[eid] = config.attackCooldown;
  }
}

function tickIdle(
  state: State,
  eid: number,
  inst: AiInstanceState,
  config: MeleeAiConfig,
  dt: number
): void {
  inst.idleTimer -= dt;
  if (inst.idleTimer <= 0) {
    inst.hovering = !inst.hovering;
    if (inst.hovering) {
      inst.idleTimer =
        config.hoverMin + Math.random() * (config.hoverMax - config.hoverMin);
    } else {
      inst.idleTimer = config.hoverMin * 0.6;
      const angle = Math.random() * Math.PI * 2;
      const r = Math.random() * config.wanderRadius * 0.6;
      inst.wanderX = inst.originX + Math.sin(angle) * r;
      inst.wanderZ = inst.originZ + Math.cos(angle) * r;
    }
  }
  if (!inst.hovering) {
    const homeDx = inst.originX - Transform.posX[eid];
    const homeDz = inst.originZ - Transform.posZ[eid];
    if (
      homeDx * homeDx + homeDz * homeDz >
      config.wanderRadius * config.wanderRadius
    ) {
      moveToward(
        state,
        eid,
        inst.originX,
        inst.originZ,
        config.wanderSpeed,
        dt
      );
    } else {
      moveToward(
        state,
        eid,
        inst.wanderX,
        inst.wanderZ,
        config.wanderSpeed,
        dt
      );
    }
  }
}
