import { defineQuery } from '../../core';
import type { State } from '../../core';
import { Transform } from '../transforms/components';
import {
  FactionComponent,
  Health,
  damageHealth,
  isHostile,
} from '../combat/components';
import { hasLineOfSight } from '../bvh/utils';
import {
  setAgentTarget,
  clearAgentTarget,
  isNavMeshReady,
  removeAgent,
} from '../navmesh';
import { NavMeshAgent } from '../navmesh/components';
import { Rigidbody } from '../physics/components';
import { markRigidbodyPoseDirty } from '../physics/utils';
import {
  AI_MODE_ATTACK,
  AI_MODE_CHASE,
  AI_MODE_DEAD,
  AI_MODE_DETECT,
  AI_MODE_IDLE,
  AI_MODE_LUNGE,
  AiStateComponent,
  aiRandom,
  type AiInstanceState,
  type MeleeAiConfig,
} from './components';

const hostilesQuery = defineQuery([Health, FactionComponent]);

/** Brief telegraph before idle→chase so packs don't instantly rush. */
const DETECT_GRACE = 0.28;
/**
 * Multiplier on {@link MeleeAiConfig.attackRange} for the end-of-lunge damage
 * window. A little forgiveness (>1.0) stops melee from whiffing when the hero
 * steps back a hair during the burst, but values near 1.5 make the first swing
 * land from well outside the attackRange that triggered it ("hit from far").
 * Kept tight (1.2) because {@link applyLungeMovement} now re-aims every frame
 * and clamps the burst inside this ring, so repeat hits connect regardless.
 */
const LUNGE_HIT_FACTOR = 1.2;
const LUNGE_BURST_SPEED = 6.0;
// Face-to-face combat ring: the creature approaches to RING_DESIRED and backs
// off if the target presses inside RING_MIN_GAP, so it holds ~1m instead of
// overlapping or attacking from far away.
const RING_DESIRED = 1.0;
const RING_MIN_GAP = 0.8;
/** Soft push between agents so packs don't stack on the same spot. */
const PEER_SEP_RADIUS = 1.35;
const PEER_SEP_STRENGTH = 1.6;
const aiAgentsQuery = defineQuery([AiStateComponent, Transform, Health]);

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
 * the nearest one that is hostile (via `isHostile`) and alive, within
 * `config.detectRange`.
 *
 * The range cut is not a behaviour change: `runMeleeAiFrame` drops any target
 * beyond `detectRange` on the next branch (mode → IDLE, target → 0). Scanning
 * past it only burned `isHostile` checks and — with `requireLineOfSight` — one
 * BVH raycast per idle creature per frame, aimed across the whole map.
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
  const requireLos = config.requireLineOfSight === true;
  const maxDist = config.detectRange > 0 ? config.detectRange : Infinity;
  let bestEid = 0;
  let bestDist = Infinity;
  for (const candidate of hostilesQuery(state.world)) {
    if (candidate === eid) continue;
    if (!entityAlive(state, candidate)) continue;
    const cx = Transform.posX[candidate];
    const cz = Transform.posZ[candidate];
    const d = distanceXZ(ox, oz, cx, cz);
    // Cheap rejects (array reads) before the faction lookup and the raycast.
    if (d > maxDist || d >= bestDist) continue;
    if (!isHostile(state, eid, candidate)) continue;
    // LOS gate: skip candidates hidden behind terrain/props. The explicit
    // targetEid path above bypasses this so a locked target stays locked even
    // if a pillar briefly breaks sight mid-fight. hasLineOfSight is permissive
    // (returns true) when no BVH geometry is registered.
    if (requireLos && !hasLineOfSight(state, ox, oz, cx, cz)) continue;
    bestDist = d;
    bestEid = candidate;
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
  state: State,
  eid: number,
  inst: AiInstanceState,
  config: MeleeAiConfig,
  targetEid: number,
  dt: number
): void {
  const x = Transform.posX[eid];
  const z = Transform.posZ[eid];
  // Re-aim the burst each frame toward the target's *current* position. The
  // direction was captured once at windup start (tickAttack); without a re-aim
  // a strafing/struck target (or the creature's own orbit) leaves the lunge
  // travelling along a stale tangent, so the burst overshoots and the damage
  // step at the end misses. This keeps the dash pointing at the hero.
  if (targetEid > 0) {
    const tdx = Transform.posX[targetEid] - x;
    const tdz = Transform.posZ[targetEid] - z;
    const tlen = Math.hypot(tdx, tdz);
    if (tlen > 1e-3) {
      inst.lungeDirX = tdx / tlen;
      inst.lungeDirZ = tdz / tlen;
    }
  }
  let nx = x + inst.lungeDirX * LUNGE_BURST_SPEED * dt;
  let nz = z + inst.lungeDirZ * LUNGE_BURST_SPEED * dt;
  if (targetEid > 0) {
    const pdx = nx - Transform.posX[targetEid];
    const pdz = nz - Transform.posZ[targetEid];
    const pd = Math.sqrt(pdx * pdx + pdz * pdz);
    // Too-close clamp (unchanged): never press inside the standoff.
    if (pd < config.lungeStandoff) {
      const ux = pd > 1e-3 ? pdx / pd : -inst.lungeDirX;
      const uz = pd > 1e-3 ? pdz / pd : -inst.lungeDirZ;
      nx = Transform.posX[targetEid] + ux * config.lungeStandoff;
      nz = Transform.posZ[targetEid] + uz * config.lungeStandoff;
    } else {
      // Too-far clamp: pin inside damage window so end-of-lunge hit connects.
      const maxReach = Math.max(
        config.lungeStandoff,
        config.attackRange * LUNGE_HIT_FACTOR
      );
      if (pd > maxReach) {
        const ux = pdx / pd;
        const uz = pdz / pd;
        nx = Transform.posX[targetEid] + ux * maxReach;
        nz = Transform.posZ[targetEid] + uz * maxReach;
      }
    }
  }
  Transform.posX[eid] = nx;
  Transform.posZ[eid] = nz;
  Transform.dirty[eid] = 1;
  // `<Creature>` owns XZ via kinematic CCT: PhysicsRapierSync overwrites
  // Transform from the body each fixed step. Transform-only dashes were a
  // one-frame lie — first swing could "hit from far", then the body stayed
  // put and later swings whiffed. Push XZ into Rigidbody + teleport flag.
  if (state.hasComponent(eid, Rigidbody)) {
    Rigidbody.posX[eid] = nx;
    Rigidbody.posZ[eid] = nz;
    markRigidbodyPoseDirty(eid);
  }
}

/** Re-enable nav after an aborted/finished lunge so steering can resume. */
function resetLungeState(
  state: State,
  eid: number,
  inst: AiInstanceState
): void {
  if (inst.lungePhase === 'ready') return;
  inst.lungePhase = 'ready';
  inst.lungeTimer = 0;
  // Lunge aborted (lost target / leashed / died): un-suspend so navmesh
  // steering resumes. enabled was never cleared (we suspend, not disable).
  if (state.hasComponent(eid, NavMeshAgent)) {
    NavMeshAgent.suspended[eid] = 0;
  }
}

/** Walk back to spawn instead of idling wherever the chase ended. */
function beginReturnHome(inst: AiInstanceState): void {
  inst.hovering = false;
  inst.wanderX = inst.originX;
  inst.wanderZ = inst.originZ;
  inst.idleTimer = 2.5 + aiRandom() * 1.5;
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

  if (comp.mode[eid] === AI_MODE_DEAD) {
    // Stale DEAD on a live eid (recycled slot, AiStateComponent is a raw array
    // never cleared on recycle): recover so a respawned full-HP creature isn't
    // treated as dead.
    if (!entityDead(eid)) {
      comp.mode[eid] = AI_MODE_IDLE;
      comp.target[eid] = 0;
    }
    return;
  }

  if (!inst.originSet) {
    inst.originX = Transform.posX[eid];
    inst.originZ = Transform.posZ[eid];
    inst.originSet = true;
    comp.leash[eid] = config.leashRadius;
    // Steering is navmesh-driven: ensure the entity carries a NavMeshAgent so
    // NavMeshAgentSystem creates a crowd agent and writes its position (XZ)
    // back into Transform each frame. Facing/yaw is owned by presentation
    // (``NavMeshAgent.faceVelocity=0``) or by navmesh when faceVelocity=1.
    if (!state.hasComponent(eid, NavMeshAgent)) {
      state.addComponent(eid, NavMeshAgent);
    }
    NavMeshAgent.speed[eid] = config.chaseSpeed;
    NavMeshAgent.radius[eid] = 0.4;
    NavMeshAgent.height[eid] = 1.0;
    NavMeshAgent.enabled[eid] = 1;
  }

  if (entityDead(eid)) {
    if (comp.mode[eid] !== AI_MODE_DEAD) {
      clearAgentTarget(state, eid);
      removeAgent(state, eid);
      NavMeshAgent.enabled[eid] = 0;
    }
    resetLungeState(state, eid, inst);
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

  // In-flight lunge must always tick — the outer range checks used to early-
  // return while mode stayed LUNGE (jump clip clamped forever, nav disabled).
  if (inst.lungePhase !== 'ready') {
    if (
      targetEid > 0 &&
      withinLeash(
        inst,
        Transform.posX[targetEid],
        Transform.posZ[targetEid],
        config.leashRadius
      )
    ) {
      const dist = distanceXZ(
        Transform.posX[eid],
        Transform.posZ[eid],
        Transform.posX[targetEid],
        Transform.posZ[targetEid]
      );
      tickAttack(state, eid, inst, config, targetEid, dist, dt);
    } else {
      resetLungeState(state, eid, inst);
      comp.mode[eid] = AI_MODE_IDLE;
      comp.target[eid] = 0;
      beginReturnHome(inst);
      tickIdle(state, eid, inst, config, dt);
    }
    return;
  }

  const mode = comp.mode[eid];

  if (targetEid <= 0) {
    beginReturnHome(inst);
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
    beginReturnHome(inst);
    tickIdle(state, eid, inst, config, dt);
    return;
  }

  if (dist <= config.attackRange) {
    if (mode === AI_MODE_DETECT) {
      comp.mode[eid] = AI_MODE_CHASE;
    }
    tickAttack(state, eid, inst, config, targetEid, dist, dt);
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
    } else if (mode === AI_MODE_LUNGE || mode === AI_MODE_ATTACK) {
      // Target stepped out of melee but is still in sight — resume chase.
      comp.mode[eid] = AI_MODE_CHASE;
    }
    if (comp.mode[eid] !== AI_MODE_CHASE) {
      return;
    }
    tickChase(state, eid, config, targetEid, inst, dt);
    return;
  }

  // Lost the target (out of detect): drop aggro and walk home cleanly.
  comp.mode[eid] = AI_MODE_IDLE;
  comp.target[eid] = 0;
  beginReturnHome(inst);
  tickIdle(state, eid, inst, config, dt);
}

function entityDead(eid: number): boolean {
  return Health.current[eid] <= 0;
}

function hpFraction(eid: number): number {
  const max = Health.max[eid] || 1;
  return Health.current[eid] / max;
}

/** True when the creature is below its enrage HP threshold. */
function isEnraged(eid: number, config: MeleeAiConfig): boolean {
  return (
    config.enrageBelowFrac !== undefined &&
    hpFraction(eid) < config.enrageBelowFrac
  );
}

function chaseSpeedFor(eid: number, config: MeleeAiConfig): number {
  return isEnraged(eid, config)
    ? config.chaseSpeed * (config.enrageSpeedMult ?? 1.4)
    : config.chaseSpeed;
}

/**
 * Combat steering: hold a ring around the target (approach / back off), and —
 * when configured — orbit it (strafe) instead of standing still, and back off
 * further (kite) at low HP. Reads as active circling rather than a statue.
 */
function steerCombat(
  state: State,
  eid: number,
  targetEid: number,
  inst: AiInstanceState,
  config: MeleeAiConfig,
  speed: number,
  dt: number,
  allowStrafe: boolean
): void {
  const x = Transform.posX[eid];
  const z = Transform.posZ[eid];
  const dx = Transform.posX[targetEid] - x;
  const dz = Transform.posZ[targetEid] - z;
  const dist = Math.sqrt(dx * dx + dz * dz) || 1e-3;
  const ux = dx / dist;
  const uz = dz / dist;

  const lowHp =
    config.lowHpKiteFrac !== undefined &&
    hpFraction(eid) < config.lowHpKiteFrac;
  const desired = lowHp
    ? Math.max(RING_DESIRED, config.detectRange * 0.35)
    : RING_DESIRED;

  // Radial: move to the desired stand-off band.
  let tx = x;
  let tz = z;
  if (dist > desired) {
    const k = (dist - desired) / dist;
    tx = x + dx * k;
    tz = z + dz * k;
  } else if (dist < RING_MIN_GAP) {
    const k = (RING_MIN_GAP - dist) / dist;
    tx = x - dx * k;
    tz = z - dz * k;
  }

  // Tangential orbit while closing / kiting. Light strafe is also allowed in
  // the attack ring when configured so packs circle between swings.
  const canOrbit =
    allowStrafe &&
    (config.strafe || lowHp) &&
    (dist > desired * 1.05 || (config.strafe && dist <= desired * 1.2));
  if (canOrbit) {
    inst.strafeTimer -= dt;
    if (inst.strafeTimer <= 0) {
      inst.strafeTimer = 1.2 + aiRandom() * 1.4;
      inst.strafeDir = -inst.strafeDir;
    }
    const orbit = lowHp || dist > desired * 1.15 ? 0.9 : 0.55;
    tx += -uz * inst.strafeDir * orbit;
    tz += ux * inst.strafeDir * orbit;
  }

  // Peer separation: nudge away from other living agents so packs don't overlap.
  // Half-rate (alternate frames) — visual packing stays fine at ~30 Hz.
  let sepX = 0;
  let sepZ = 0;
  if (state.world && (state.time.frameCount & 1) === 0) {
    for (const other of aiAgentsQuery(state.world)) {
      if (other === eid || entityDead(other)) continue;
      const ox = Transform.posX[other] - x;
      const oz = Transform.posZ[other] - z;
      const d2 = ox * ox + oz * oz;
      if (d2 < 1e-6 || d2 > PEER_SEP_RADIUS * PEER_SEP_RADIUS) continue;
      const d = Math.sqrt(d2);
      const push =
        ((PEER_SEP_RADIUS - d) / PEER_SEP_RADIUS) * PEER_SEP_STRENGTH;
      sepX -= (ox / d) * push;
      sepZ -= (oz / d) * push;
    }
  }
  tx += sepX;
  tz += sepZ;

  if (Math.abs(tx - x) > 0.05 || Math.abs(tz - z) > 0.05) {
    moveToward(state, eid, tx, tz, speed, dt);
  } else {
    clearAgentTarget(state, eid);
  }
}

function tickChase(
  state: State,
  eid: number,
  config: MeleeAiConfig,
  targetEid: number,
  inst: AiInstanceState,
  dt: number
): void {
  steerCombat(
    state,
    eid,
    targetEid,
    inst,
    config,
    chaseSpeedFor(eid, config),
    dt,
    true
  );
}

function tickAttack(
  state: State,
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
    // Hold the combat ring between swings; strafe when the preset asks for it.
    steerCombat(
      state,
      eid,
      targetEid,
      inst,
      config,
      chaseSpeedFor(eid, config),
      dt,
      !!config.strafe
    );
    comp.cooldown[eid] = Math.max(0, comp.cooldown[eid] - dt);
    if (comp.cooldown[eid] <= 0) {
      inst.lungePhase = 'windup';
      inst.lungeTimer = config.lungeWindup;
      const len = dist > 1e-3 ? dist : 1;
      const dx = Transform.posX[targetEid] - Transform.posX[eid];
      const dz = Transform.posZ[targetEid] - Transform.posZ[eid];
      inst.lungeDirX = dx / len;
      inst.lungeDirZ = dz / len;
      // The lunge is direct Transform motion. Suspend the crowd agent (keep it
      // alive, freeze its readback) so the dash is not overwritten — WITHOUT
      // removing + re-adding the agent, which snapped position on re-add (the
      // jitter/popping players felt at every lunge). Suspended is cleared when
      // the lunge returns to 'ready'.
      clearAgentTarget(state, eid);
      NavMeshAgent.suspended[eid] = 1;
    }
    return;
  }

  if (inst.lungePhase === 'windup') {
    // Keep ATTACK so presentation plays the attack/idle clip, not jump.
    comp.mode[eid] = AI_MODE_ATTACK;
    inst.lungeTimer -= dt;
    if (inst.lungeTimer <= 0) {
      inst.lungePhase = 'lunge';
      inst.lungeTimer = config.lungeDuration;
    }
    return;
  }

  if (inst.lungePhase === 'lunge') {
    comp.mode[eid] = AI_MODE_LUNGE;
    inst.lungeTimer -= dt;
    applyLungeMovement(state, eid, inst, config, targetEid, dt);
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

  // Recovery: plant / face, then re-arm.
  comp.mode[eid] = AI_MODE_ATTACK;
  inst.lungeTimer -= dt;
  if (inst.lungeTimer <= 0) {
    inst.lungePhase = 'ready';
    comp.cooldown[eid] = isEnraged(eid, config)
      ? config.attackCooldown * (config.enrageCooldownMult ?? 0.5)
      : config.attackCooldown;
    // Lunge done: un-suspend the crowd agent (still alive from the windup) so
    // navmesh steering resumes. No addAgent → no position snap.
    NavMeshAgent.suspended[eid] = 0;
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
        config.hoverMin + aiRandom() * (config.hoverMax - config.hoverMin);
    } else {
      inst.idleTimer = config.hoverMin * 0.6;
      const angle = aiRandom() * Math.PI * 2;
      const r = aiRandom() * config.wanderRadius * 0.6;
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
