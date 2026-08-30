// Real melee attack. The engine resolves damage only for projectiles (bombs)
// and enemy→player AI, so the player's [J] swing previously did nothing to enemies
// (it only played the swing clip + harvested trees/rocks via Destructible).
// This module makes [J] deal damage to enemies in a frontal arc, scaling with
// the resolved attack bonus (Strength ranks + merchant sword upgrades, folded
// into playerStats.attackBonus by PlayerStatsSystem).
//
// Swing SFX + damage land near the strike peak of the attack clip (not the
// key-press edge). Quaternius-style packs are ~1.5s with the cut ~25–40% in
// and a long recovery — 0.7 of duration lands in the settle, far too late.
import {
  Health,
  PlayerGltfConfig,
  Transform,
  WorldTransform,
  addCameraShake,
  applyCctKnockback,
  damageHealth,
  defineQuery,
  getAnimator,
  getPlayerAttackClip,
  getPlayerAttackTimeScale,
  setPlayerAttackTimeScale,
  hitStop,
  isDead,
  isKeyDown,
  playSound,
  setCombatTarget,
  setPlayerFaceTarget,
  spawnFloatingText,
  spawnParticleBurst,
} from 'aigamekit-vibegame';
import type { State } from 'aigamekit-vibegame';
import { playerStats } from './skills';
import { isGamePaused } from './pause';
import { getEnemyLabel } from '../scripts/enemy-registry';
import { isBossCreature } from '../scripts/creature';
import {
  advanceChain,
  bloodthirst,
  comboDamageMult,
  consumeRiposte,
  executeMultiplier,
  finisherArcDot,
  notifyPlayerHitLanded,
  peekRiposte,
  tryVenom,
  type ChainStep,
} from './combat-mechanics';

const BASE_MELEE_DAMAGE = 16;
const MELEE_RANGE = 3.0;
const MELEE_RANGE_SQ = MELEE_RANGE * MELEE_RANGE;
// Soft-lock acquire uses a slightly wider radius than the hit cone.
const LOCK_RANGE_SQ = (MELEE_RANGE + 0.6) * (MELEE_RANGE + 0.6);
// Frontal cone: hit anything within ~90° of the swing direction (faces target).
const MELEE_ARC_DOT = Math.cos((90 * Math.PI) / 180);
const MELEE_VERTICAL = 2.5;
// Tightened to match the accelerated attack clip (engine default 1.4×).
const SWING_COOLDOWN = 0.36;
/** Chance of a critical on any swing; a hit from behind is always critical. */
const CRIT_CHANCE = 0.15;
const CRIT_MULTIPLIER = 2;
/** cos(70°) — how far around the target's back the bonus still applies. */
const BACKSTAB_DOT = Math.cos((70 * Math.PI) / 180);
const FACE_HOLD = 0.45;
/** Strike peak ≈27% on player sword/attack; slightly after for whoosh/hit feel. */
const SWING_IMPACT_FRACTION = 0.35;
const FALLBACK_IMPACT_DELAY = 0.22;
/** The whoosh starts this many seconds *before* contact — the sword cuts the
 * air on the way in; playing it exactly on the hit frame reads late. */
const WHOOSH_LEAD = 0.12;
// ── Impact feel (per landed blow) ──
/** SFX pitch jitter ±: repeated identical swings read as robotic. */
const PITCH_JITTER = 0.08;
/** Pushback distance/duration on a hit (crits shove harder). */
const KNOCKBACK_DISTANCE = 0.8;
const KNOCKBACK_DISTANCE_CRIT = 1.3;
const KNOCKBACK_DURATION = 0.2;
/** Freeze-frame on a connecting swing: normal / crit / killing blow. */
const HIT_STOP_SEC = 0.07;
const HIT_STOP_SEC_CRIT = 0.11;
const HIT_STOP_SEC_KILL = 0.12;
const HIT_STOP_SCALE = 0.05;
/** Base attack speed (engine default) — each swing jitters ±8% around it so
 * consecutive swings of the same clip never read identical. */
const ATTACK_TIME_SCALE_BASE = 1.4;
const ATTACK_TIME_SCALE_JITTER = 0.08;

const healthQuery = defineQuery([Health, Transform]);
const _fwd = { x: 0, z: 0 };
const _targetFwd = { x: 0, z: 0 };
let swingTimer = 0;
// Seconds since the player last performed an attack (melee or skill) — feeds
// the guard-idle window: stay in the combat stance for a few seconds after
// striking instead of dropping straight back to the relaxed idle.
let timeSinceAttack = Infinity;

export function notePlayerAttacked(): void {
  timeSinceAttack = 0;
}

export function secondsSincePlayerAttack(): number {
  return timeSinceAttack;
}

// Live diag counters (read via `meleeDiag()` from the console during dev):
// swings = presses scheduled; impacts = landSwing runs; inRange/arcLocked =
// candidates passing each gate in the last impact; hits = blows applied.
const diag = { swings: 0, impacts: 0, inRange: 0, arcLocked: 0, hits: 0 };
export function meleeDiag(): Record<string, number> {
  return { ...diag };
}
let jPressed = false;
let faceHoldTimer = 0;
let meleeOwnsFace = false;

interface PendingSwing {
  /** Seconds until the whoosh starts (WHOOSH_LEAD before contact). */
  soundIn: number;
  /** Seconds until the blow lands (strike peak). */
  delay: number;
  player: number;
  aimX: number;
  aimZ: number;
  dmg: number;
  merchant: number | null;
  /** Position of this swing in the [J][J][J] escalation chain. */
  chain: ChainStep;
}

let pending: PendingSwing | null = null;

function playerForward(player: number): void {
  const x = WorldTransform.rotX[player];
  const y = WorldTransform.rotY[player];
  const z = WorldTransform.rotZ[player];
  const w = WorldTransform.rotW[player];
  let fx = 2 * (x * z + w * y);
  let fz = 1 - 2 * (x * x + y * y);
  const len = Math.hypot(fx, fz) || 1;
  _fwd.x = fx / len;
  _fwd.z = fz / len;
}

function labelFor(state: State, eid: number): string {
  return getEnemyLabel(eid) || state.getEntityName(eid) || 'Enemy';
}

/** Seconds until the whoosh/hit frame (strike peak of the attack clip). The
 * engine plays the clip at `getPlayerAttackTimeScale()`, so the wall-clock
 * delay shrinks by the same rate or the blow would land after the visual. */
function swingImpactDelay(state: State, player: number): number {
  if (!state.hasComponent(player, PlayerGltfConfig))
    return FALLBACK_IMPACT_DELAY;
  const regIdx = PlayerGltfConfig.animatorRegistryIndex[player];
  const animator = regIdx ? getAnimator(state, regIdx) : undefined;
  if (!animator) return FALLBACK_IMPACT_DELAY;
  // Prefer the context clip the engine will play (sword/axe/spear/chop/mine).
  const hint = getPlayerAttackClip();
  // Mirrored combo variants ("<clip>_m") are runtime-built by the engine —
  // make sure the clip exists before timing the swing off its duration.
  if (hint?.endsWith('_m')) animator.ensureMirroredClip(hint);
  const keywords = hint
    ? [hint, 'attack', 'swing', 'punch', 'slash']
    : [
        'sword',
        'axe',
        'spear',
        'chop',
        'mine',
        'attack',
        'swing',
        'punch',
        'slash',
      ];
  let attackName = '';
  for (const kw of keywords) {
    const lower = kw.toLowerCase();
    const exact = animator.clipNames.find((n) => n.toLowerCase() === lower);
    const hit =
      exact ?? animator.clipNames.find((n) => n.toLowerCase().includes(lower));
    if (hit) {
      attackName = hit;
      break;
    }
  }
  // Alternating-combo hints may ask for a "_m" mirrored clip that only exists
  // once built — ensure it before reading the duration (else delay=0 fallback).
  if (attackName.endsWith('_m')) animator.ensureMirroredClip(attackName);
  const duration = attackName
    ? (animator.clips.get(attackName)?.duration ?? 0)
    : 0;
  return duration > 0
    ? (duration * SWING_IMPACT_FRACTION) / getPlayerAttackTimeScale()
    : FALLBACK_IMPACT_DELAY;
}

/** Forward vector (XZ) of any entity, from its world quaternion. */
function facingOf(eid: number, out: { x: number; z: number }): void {
  const x = WorldTransform.rotX[eid];
  const y = WorldTransform.rotY[eid];
  const z = WorldTransform.rotZ[eid];
  const w = WorldTransform.rotW[eid];
  const fx = 2 * (x * z + w * y);
  const fz = 1 - 2 * (x * x + y * y);
  const len = Math.hypot(fx, fz) || 1;
  out.x = fx / len;
  out.z = fz / len;
}

/**
 * Backstab test: the blow lands on the target's back when the player approaches
 * along the direction the target is already facing. `apX/apZ` is the normalised
 * player→target vector, so agreeing with the target's own forward means we are
 * behind it.
 */
function isBackstab(target: number, apX: number, apZ: number): boolean {
  facingOf(target, _targetFwd);
  return _targetFwd.x * apX + _targetFwd.z * apZ >= BACKSTAB_DOT;
}

function landSwing(state: State, swing: PendingSwing): void {
  diag.impacts++;
  diag.inRange = 0;
  diag.arcLocked = 0;
  // Whoosh already played WHOOSH_LEAD before contact (updateMelee); what fires
  // here is the hit itself: damage, feedback and impact weight.
  const hx = Transform.posX[swing.player];
  const hy = Transform.posY[swing.player];
  const hz = Transform.posZ[swing.player];
  const finisher = swing.chain.finisher;
  // A finisher cleaves wider and reaches further than a poke.
  const rangeSq = finisher
    ? (MELEE_RANGE + 0.5) * (MELEE_RANGE + 0.5)
    : MELEE_RANGE_SQ;
  const arcDot = finisher ? finisherArcDot() : MELEE_ARC_DOT;
  // Combo counter (landed hits) + riposte (after a parry) apply to the whole
  // swing; the execute bonus rolls per victim.
  const comboMult = comboDamageMult();
  const riposteMult = peekRiposte();

  let hits = 0;
  let critHit = false;
  let killed = false;
  let dealt = 0;

  for (const e of healthQuery(state.world)) {
    if (e === swing.player || e === swing.merchant || isDead(e)) continue;
    const dx = Transform.posX[e] - hx;
    const dz = Transform.posZ[e] - hz;
    const dy = Transform.posY[e] - hy;
    const d2 = dx * dx + dz * dz;
    if (d2 > rangeSq || Math.abs(dy) > MELEE_VERTICAL) continue;
    diag.inRange++;
    const dist = Math.sqrt(d2) || 1;
    const apX = dx / dist;
    const apZ = dz / dist;
    if (swing.aimX * apX + swing.aimZ * apZ < arcDot) continue;
    diag.arcLocked++;

    // Crit: a flat roll, or guaranteed when the hit comes from behind. Flat
    // damage every swing read as "hitting a wall"; the roll plus the positional
    // guarantee gives the fight a reason to circle instead of standing still.
    const back = isBackstab(e, apX, apZ);
    const crit = back || Math.random() < CRIT_CHANCE;
    // Stack order: chain (3rd blow) → combo counter → riposte → execute.
    let dmg =
      swing.dmg * swing.chain.mult * comboMult * (riposteMult > 1 ? 2 : 1);
    const exec = executeMultiplier(e);
    if (exec > 1) {
      dmg *= exec;
      spawnFloatingText(state, 'EXECUTADO!', {
        x: Transform.posX[e],
        y: Transform.posY[e] + 2.8,
        z: Transform.posZ[e],
        color: '#ff4a4a',
        duration: 0.9,
      });
    }
    dmg = crit ? Math.round(dmg * CRIT_MULTIPLIER) : Math.round(dmg);

    hits++;
    dealt += dmg;
    diag.hits++;
    if (crit) critHit = true;
    damageHealth(e, dmg, swing.player);
    if (isDead(e)) killed = true;
    setCombatTarget(e, { label: labelFor(state, e) });
    tryVenom(state, e);
    // Impact weight: shove the victim along the blow (bosses have poise —
    // the sword stops on them, they don't move). Stagger/hit-clip/flash are
    // the creature's own reaction (creature.ts HP-drop).
    if (!isBossCreature(e)) {
      const knock = crit
        ? KNOCKBACK_DISTANCE_CRIT
        : finisher
          ? KNOCKBACK_DISTANCE_CRIT * 0.85
          : KNOCKBACK_DISTANCE;
      applyCctKnockback(state, e, apX, apZ, knock, KNOCKBACK_DURATION);
    }
    if (crit) {
      playSound('swing', {
        originEid: e,
        pitch: 1 + (Math.random() * 2 - 1) * PITCH_JITTER,
      });
      spawnFloatingText(state, back ? 'PELAS COSTAS!' : 'CRÍTICO!', {
        x: Transform.posX[e],
        y: Transform.posY[e] + 2.6,
        z: Transform.posZ[e],
        color: back ? '#ffd24a' : '#ff8a33',
        duration: 0.9,
      });
    }
    spawnParticleBurst(state, {
      x: Transform.posX[e],
      y: Transform.posY[e] + 1.2,
      z: Transform.posZ[e],
      preset: 'slash',
      count: 1,
      duration: 0.25,
    });
    spawnParticleBurst(state, {
      x: Transform.posX[e],
      y: Transform.posY[e] + 1.0,
      z: Transform.posZ[e],
      preset: 'sparks',
      count: crit ? 16 : 6,
      duration: crit ? 0.55 : 0.35,
    });
  }

  if (hits > 0) {
    // A riposte-boosted swing consumed the parry window.
    if (riposteMult > 1) consumeRiposte();
    notifyPlayerHitLanded(hits);
    bloodthirst(state, swing.player, dealt);
  }

  // Connecting swings freeze the world for a beat and kick the camera — the
  // heavier the blow (finisher / crit / kill), the longer and harder. A
  // finisher lands with a ground shockwave ring even on a whiff-adjacent clip.
  if (hits > 0) {
    hitStop(
      state,
      killed
        ? HIT_STOP_SEC_KILL
        : critHit || finisher
          ? HIT_STOP_SEC_CRIT
          : HIT_STOP_SEC,
      HIT_STOP_SCALE
    );
    addCameraShake(killed ? 0.5 : critHit ? 0.4 : finisher ? 0.38 : 0.22);
    if (finisher) {
      spawnParticleBurst(state, {
        x: hx + swing.aimX * 1.2,
        y: hy + 0.4,
        z: hz + swing.aimZ * 1.2,
        preset: 'explosion',
        count: 18,
        duration: 0.55,
      });
      spawnFloatingText(state, 'GOLPE FINAL!', {
        x: hx,
        y: hy + 2.2,
        z: hz,
        color: '#ff8a33',
        duration: 0.8,
      });
    }
  }
  // Re-roll the NEXT swing's speed here (post-impact): the current swing was
  // already scheduled with the previous scale, so engine + game stay in sync.
  setPlayerAttackTimeScale(
    ATTACK_TIME_SCALE_BASE *
      (1 + (Math.random() * 2 - 1) * ATTACK_TIME_SCALE_JITTER)
  );
}

/**
 * Poll [J] and, on the press edge (rate-limited by a swing cooldown), soft-lock
 * the nearest enemy and face them. Swing SFX + damage fire near the strike
 * peak (~35% of the attack clip), not on the key edge.
 */
export function updateMelee(state: State, player: number, dt: number): void {
  timeSinceAttack += dt;
  if (swingTimer > 0) swingTimer = Math.max(0, swingTimer - dt);
  if (faceHoldTimer > 0) {
    faceHoldTimer = Math.max(0, faceHoldTimer - dt);
    if (faceHoldTimer <= 0 && meleeOwnsFace) {
      setPlayerFaceTarget(null);
      meleeOwnsFace = false;
    }
  }

  if (pending) {
    // Whoosh first: the blade cuts the air slightly before contact — a swing
    // SFX landing on the hit frame (or worse, on the key edge) reads late.
    if (pending.soundIn > 0) {
      pending.soundIn -= dt;
      if (
        pending.soundIn <= 0 &&
        !isGamePaused() &&
        pending.player > 0 &&
        !isDead(pending.player)
      ) {
        playSound('swing', {
          originEid: pending.player,
          pitch: 1 + (Math.random() * 2 - 1) * PITCH_JITTER,
        });
      }
    }
    pending.delay -= dt;
    if (pending.delay <= 0) {
      const swing = pending;
      pending = null;
      if (!isGamePaused() && swing.player > 0 && !isDead(swing.player)) {
        landSwing(state, swing);
      }
    }
  }

  if (isGamePaused() || player <= 0 || isDead(player)) {
    jPressed = isKeyDown('KeyJ');
    return;
  }

  const down = isKeyDown('KeyJ');
  const edge = down && !jPressed;
  jPressed = down;
  if (!edge || swingTimer > 0 || pending) return;

  const delay = swingImpactDelay(state, player);
  swingTimer = Math.max(SWING_COOLDOWN, delay + 0.05);

  const merchant = state.getEntityByName('merchant');
  playerForward(player);
  const hx = Transform.posX[player];
  const hy = Transform.posY[player];
  const hz = Transform.posZ[player];
  const dmg = BASE_MELEE_DAMAGE + playerStats.attackBonus;

  // Soft-lock: nearest living enemy in lock range (full circle).
  let lockEid = -1;
  let lockBest = Infinity;
  let lockDx = 0;
  let lockDz = 0;
  for (const e of healthQuery(state.world)) {
    if (e === player || e === merchant || isDead(e)) continue;
    const dx = Transform.posX[e] - hx;
    const dz = Transform.posZ[e] - hz;
    const dy = Transform.posY[e] - hy;
    const d2 = dx * dx + dz * dz;
    if (d2 > LOCK_RANGE_SQ || Math.abs(dy) > MELEE_VERTICAL) continue;
    if (d2 < lockBest) {
      lockBest = d2;
      lockEid = e;
      lockDx = dx;
      lockDz = dz;
    }
  }

  // Swing direction: toward soft-lock target when one exists, else body forward.
  let aimX = _fwd.x;
  let aimZ = _fwd.z;
  if (lockEid >= 0) {
    const dist = Math.sqrt(lockBest) || 1;
    aimX = lockDx / dist;
    aimZ = lockDz / dist;
    setPlayerFaceTarget(Transform.posX[lockEid], Transform.posZ[lockEid]);
    meleeOwnsFace = true;
    faceHoldTimer = FACE_HOLD;
    setCombatTarget(lockEid, { label: labelFor(state, lockEid) });
  }

  pending = {
    soundIn: Math.max(0.02, delay - WHOOSH_LEAD),
    delay,
    player,
    aimX,
    aimZ,
    dmg,
    merchant,
    chain: advanceChain(),
  };
  diag.swings++;
  notePlayerAttacked();
}

/** HMR/teardown reset of the swing edge state. */
export function clearMelee(): void {
  swingTimer = 0;
  timeSinceAttack = Infinity;
  jPressed = false;
  faceHoldTimer = 0;
  pending = null;
  if (meleeOwnsFace) {
    setPlayerFaceTarget(null);
    meleeOwnsFace = false;
  }
}
