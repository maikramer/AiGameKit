// Real melee attack. The engine resolves damage only for projectiles (bombs)
// and enemy→hero AI, so the hero's [J] swing previously did nothing to enemies
// (it only played the swing clip + harvested trees/rocks via Destructible).
// This module makes [J] deal damage to enemies in a frontal arc, scaling with
// the resolved attack bonus (Strength ranks + merchant sword upgrades, folded
// into heroStats.attackBonus by HeroStatsSystem).
//
// Swing SFX + damage land near the strike peak of the attack clip (not the
// key-press edge). Quaternius-style packs are ~1.5s with the cut ~25–40% in
// and a long recovery — 0.7 of duration lands in the settle, far too late.
import {
  Health,
  PlayerGltfConfig,
  Transform,
  WorldTransform,
  animatorRegistry,
  damageHealth,
  defineQuery,
  getPlayerAttackClip,
  isDead,
  isKeyDown,
  playSound,
  setCombatTarget,
  setPlayerFaceTarget,
  spawnParticleBurst,
} from 'vibegame';
import type { State } from 'vibegame';
import { heroStats } from './skills';
import { isGamePaused } from './pause';
import { getEnemyLabel } from '../scripts/enemy-registry';

const BASE_MELEE_DAMAGE = 16;
const MELEE_RANGE = 3.0;
const MELEE_RANGE_SQ = MELEE_RANGE * MELEE_RANGE;
// Soft-lock acquire uses a slightly wider radius than the hit cone.
const LOCK_RANGE_SQ = (MELEE_RANGE + 0.6) * (MELEE_RANGE + 0.6);
// Frontal cone: hit anything within ~90° of the swing direction (faces target).
const MELEE_ARC_DOT = Math.cos((90 * Math.PI) / 180);
const MELEE_VERTICAL = 2.5;
const SWING_COOLDOWN = 0.42;
const FACE_HOLD = 0.45;
/** Strike peak ≈27% on hero sword/attack; slightly after for whoosh/hit feel. */
const SWING_IMPACT_FRACTION = 0.35;
const FALLBACK_IMPACT_DELAY = 0.22;

const healthQuery = defineQuery([Health, Transform]);
const _fwd = { x: 0, z: 0 };
let swingTimer = 0;
let jPressed = false;
let faceHoldTimer = 0;
let meleeOwnsFace = false;

interface PendingSwing {
  delay: number;
  hero: number;
  aimX: number;
  aimZ: number;
  dmg: number;
  merchant: number | null;
}

let pending: PendingSwing | null = null;

function heroForward(hero: number): void {
  const x = WorldTransform.rotX[hero];
  const y = WorldTransform.rotY[hero];
  const z = WorldTransform.rotZ[hero];
  const w = WorldTransform.rotW[hero];
  let fx = 2 * (x * z + w * y);
  let fz = 1 - 2 * (x * x + y * y);
  const len = Math.hypot(fx, fz) || 1;
  _fwd.x = fx / len;
  _fwd.z = fz / len;
}

function labelFor(state: State, eid: number): string {
  return getEnemyLabel(eid) || state.getEntityName(eid) || 'Enemy';
}

/** Seconds until the whoosh/hit frame (strike peak of the attack clip). */
function swingImpactDelay(state: State, hero: number): number {
  if (!state.hasComponent(hero, PlayerGltfConfig)) return FALLBACK_IMPACT_DELAY;
  const regIdx = PlayerGltfConfig.animatorRegistryIndex[hero];
  const animator = regIdx ? animatorRegistry.get(regIdx) : undefined;
  if (!animator) return FALLBACK_IMPACT_DELAY;
  // Prefer the context clip the engine will play (sword/axe/spear/chop/mine).
  const hint = getPlayerAttackClip();
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
  const duration = attackName
    ? (animator.clips.get(attackName)?.duration ?? 0)
    : 0;
  return duration > 0
    ? duration * SWING_IMPACT_FRACTION
    : FALLBACK_IMPACT_DELAY;
}

function landSwing(state: State, swing: PendingSwing): void {
  playSound('swing', { originEid: swing.hero });

  const hx = Transform.posX[swing.hero];
  const hy = Transform.posY[swing.hero];
  const hz = Transform.posZ[swing.hero];

  for (const e of healthQuery(state.world)) {
    if (e === swing.hero || e === swing.merchant || isDead(e)) continue;
    const dx = Transform.posX[e] - hx;
    const dz = Transform.posZ[e] - hz;
    const dy = Transform.posY[e] - hy;
    const d2 = dx * dx + dz * dz;
    if (d2 > MELEE_RANGE_SQ || Math.abs(dy) > MELEE_VERTICAL) continue;
    const dist = Math.sqrt(d2) || 1;
    if ((swing.aimX * dx + swing.aimZ * dz) / dist < MELEE_ARC_DOT) continue;
    damageHealth(e, swing.dmg);
    setCombatTarget(e, { label: labelFor(state, e) });
    spawnParticleBurst(state, {
      x: Transform.posX[e],
      y: Transform.posY[e] + 1.0,
      z: Transform.posZ[e],
      preset: 'sparks',
      count: 6,
      duration: 0.35,
    });
  }
}

/**
 * Poll [J] and, on the press edge (rate-limited by a swing cooldown), soft-lock
 * the nearest enemy and face them. Swing SFX + damage fire near the strike
 * peak (~35% of the attack clip), not on the key edge.
 */
export function updateMelee(state: State, hero: number, dt: number): void {
  if (swingTimer > 0) swingTimer = Math.max(0, swingTimer - dt);
  if (faceHoldTimer > 0) {
    faceHoldTimer = Math.max(0, faceHoldTimer - dt);
    if (faceHoldTimer <= 0 && meleeOwnsFace) {
      setPlayerFaceTarget(null);
      meleeOwnsFace = false;
    }
  }

  if (pending) {
    pending.delay -= dt;
    if (pending.delay <= 0) {
      const swing = pending;
      pending = null;
      if (!isGamePaused() && swing.hero > 0 && !isDead(swing.hero)) {
        landSwing(state, swing);
      }
    }
  }

  if (isGamePaused() || hero <= 0 || isDead(hero)) {
    jPressed = isKeyDown('KeyJ');
    return;
  }

  const down = isKeyDown('KeyJ');
  const edge = down && !jPressed;
  jPressed = down;
  if (!edge || swingTimer > 0 || pending) return;

  const delay = swingImpactDelay(state, hero);
  swingTimer = Math.max(SWING_COOLDOWN, delay + 0.05);

  const merchant = state.getEntityByName('merchant');
  heroForward(hero);
  const hx = Transform.posX[hero];
  const hy = Transform.posY[hero];
  const hz = Transform.posZ[hero];
  const dmg = BASE_MELEE_DAMAGE + heroStats.attackBonus;

  // Soft-lock: nearest living enemy in lock range (full circle).
  let lockEid = -1;
  let lockBest = Infinity;
  let lockDx = 0;
  let lockDz = 0;
  for (const e of healthQuery(state.world)) {
    if (e === hero || e === merchant || isDead(e)) continue;
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
    delay,
    hero,
    aimX,
    aimZ,
    dmg,
    merchant,
  };
}

/** HMR/teardown reset of the swing edge state. */
export function clearMelee(): void {
  swingTimer = 0;
  jPressed = false;
  faceHoldTimer = 0;
  pending = null;
  if (meleeOwnsFace) {
    setPlayerFaceTarget(null);
    meleeOwnsFace = false;
  }
}
