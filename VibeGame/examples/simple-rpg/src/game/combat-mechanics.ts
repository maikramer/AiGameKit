// Combat depth mechanics layered on top of the engine combat plugin:
//   ── Attack chain [J][J][J] ── consecutive swings escalate: 3rd blow is a
//      finisher (bigger damage, wider arc, shockwave).
//   ── Combo counter ── every landed player hit builds a counter (+2% damage
//      per hit, cap +30%); taking a hit or idling 2.5 s resets it. HUD top.
//   ── Guard / Parry [L] ── hold to block (70% reduction, slow walk); the
//      first 0.22 s is a parry window: the blow is negated, the attacker is
//      staggered and the next swing within 1.5 s is a ×2 riposte. Built on
//      the engine `registerDamageModifier` pipeline (blocks BEFORE HP moves).
//   ── Perfect dodge ── dashing [C] grants brief i-frames; dashing while an
//      enemy is mid-windup (telegraph glow) triggers slow-mo + fury (+10
//      attack for 4 s).
//   ── Execute ── blows against enemies under 15% HP deal double damage.
//   ── Bloodthirst ── melee heals 8% of the damage dealt.
//   ── Venom blade ── 20% of melee hits poison (rpg-status DoT).
import {
  AiStateComponent,
  AI_LUNGE_PHASE_WINDUP,
  AI_MODE_ATTACK,
  Health,
  Transform,
  WorldTransform,
  addCameraShake,
  applyCctKnockback,
  applyStatus,
  defineQuery,
  grantInvulnerability,
  healHealth,
  hitStop,
  isDead,
  isKeyDown,
  playSound,
  registerDamageModifier,
  spawnFloatingText,
  spawnParticleBurst,
  staggerAi,
} from 'vibegame';
import type { State } from 'vibegame';
import { isGamePaused } from './pause';
import { isBossCreature } from '../scripts/creature';
import { playerStats } from './skills';

// ── Attack chain (per [J] press, not per hit) ────────────────────────────────
const CHAIN_WINDOW = 1.1;
const CHAIN_MULTS = [1, 1.15, 1.7] as const;
const FINISHER_ARC_DOT = Math.cos((70 * Math.PI) / 180); // 140° cleave

// ── Combo counter (per landed hit, any player source) ────────────────────────
const COMBO_WINDOW = 2.5;
const COMBO_BONUS_PER_HIT = 0.02;
const COMBO_BONUS_CAP = 0.3;

// ── Guard / parry ────────────────────────────────────────────────────────────
const BLOCK_KEY = 'KeyL';
/** Reduction applied while guarding after the parry window. */
const BLOCK_DAMAGE_MULT = 0.3;
/** Movement scale while guarding (read by PlayerStatsSystem). */
export const BLOCK_SPEED_MULT = 0.55;
/** Frontal cone that the guard covers (cos 75°). */
const BLOCK_ARC_DOT = Math.cos((75 * Math.PI) / 180);
/** Seconds after raising guard during which a blow is parried. */
const PARRY_WINDOW = 0.22;
const PARRY_STAGGER = 1.4;
const PARRY_KNOCKBACK = 1.2;
/** Riposte window after a successful parry. */
const RIPOSTE_WINDOW = 1.5;
const RIPOSTE_MULT = 2;
/** Bosses have poise: a parry only chips 50% off and never staggers. */
const PARRY_BOSS_DAMAGE_MULT = 0.5;

// ── Perfect dodge / fury ─────────────────────────────────────────────────────
/** Dodge i-frames granted by a dash. */
export const DODGE_IFRAMES = 0.3;
/** Extra i-frames on a perfect dodge (slow-mo already stretches it). */
const PERFECT_DODGE_IFRAMES = 0.5;
const PERFECT_DODGE_RADIUS_SQ = 4.5 * 4.5;
const PERFECT_DODGE_RADIUS_ATTACK_SQ = 3.2 * 3.2;
const SLOWMO_DURATION = 0.55;
const SLOWMO_SCALE = 0.35;
const FURY_BONUS = 10;
const FURY_DURATION = 4;

// ── Execute / bloodthirst / venom ────────────────────────────────────────────
/** HP fraction at or below which blows execute (×2 damage). */
const EXECUTE_HP_FRAC = 0.15;
/** Fraction of dealt melee damage returned as HP. */
const BLOODTHIRST_FRACTION = 0.08;
const BLOODTHIRST_CAP = 8;
/** Chance a melee hit poisons the victim. */
const VENOM_CHANCE = 0.2;

// ── Module state (reset by clearCombatMechanics on HMR) ─────────────────────
let time = 0;
let blockHeld = false;
let blocking = false;
let blockStartTime = -1;
let riposteTimer = 0;
let chainStep = -1;
let chainTimer = 0;
let comboHits = 0;
let comboTimer = 0;

export interface ChainStep {
  /** 0-based index of this swing within the chain. */
  step: number;
  /** Damage multiplier for this swing. */
  mult: number;
  /** True on the escalating 3rd+ blow (wider arc + shockwave feel). */
  finisher: boolean;
}

/** Advance the [J] chain; call once per swing press. */
export function advanceChain(): ChainStep {
  chainStep = chainTimer > 0 ? chainStep + 1 : 0;
  chainTimer = CHAIN_WINDOW;
  const step = Math.min(chainStep, CHAIN_MULTS.length - 1);
  return {
    step,
    mult: CHAIN_MULTS[step] as number,
    finisher: chainStep >= CHAIN_MULTS.length - 1,
  };
}

export function finisherArcDot(): number {
  return FINISHER_ARC_DOT;
}

/** Damage bonus from the live combo counter (1 .. 1.3). */
export function comboDamageMult(): number {
  return 1 + Math.min(COMBO_BONUS_CAP, comboHits * COMBO_BONUS_PER_HIT);
}

/** Feed the combo counter after landed player hits (melee, skills, bombs). */
export function notifyPlayerHitLanded(hits: number): void {
  if (hits <= 0) return;
  comboHits += hits;
  comboTimer = COMBO_WINDOW;
  bumpComboHud();
}

/** Called when the PLAYER takes damage — a real blow breaks the combo. */
export function notifyPlayerDamaged(): void {
  if (comboHits === 0) return;
  comboHits = 0;
  comboTimer = 0;
  updateComboHud();
}

/** Riposte multiplier without consuming it (×2 inside the window). */
export function peekRiposte(): number {
  return riposteTimer > 0 ? RIPOSTE_MULT : 1;
}

/** Clear the riposte window — call when a riposte-boosted blow lands. */
export function consumeRiposte(): void {
  riposteTimer = 0;
}

/** Execute bonus for a target: ×2 when at/below 15% HP, else ×1. */
export function executeMultiplier(eid: number): number {
  const max = Health.max[eid];
  return max > 0 && Health.current[eid] / max <= EXECUTE_HP_FRAC ? 2 : 1;
}

/** Bloodthirst: heal a fraction of the damage dealt by a melee swing. */
export function bloodthirst(state: State, player: number, dealt: number): void {
  const heal = Math.min(
    BLOODTHIRST_CAP,
    Math.round(dealt * BLOODTHIRST_FRACTION)
  );
  if (heal < 1 || isDead(player)) return;
  healHealth(player, heal);
  spawnFloatingText(state, `+${heal}`, {
    x: Transform.posX[player],
    y: Transform.posY[player] + 1.5,
    z: Transform.posZ[player],
    color: '#ff6a6a',
    size: 0.42,
    duration: 0.7,
  });
}

/** Venom blade: roll a 20% chance to poison the victim (DoT via rpg-status). */
export function tryVenom(state: State, eid: number): void {
  if (Math.random() >= VENOM_CHANCE) return;
  applyStatus(state, eid, 'poison');
  spawnParticleBurst(state, {
    x: Transform.posX[eid],
    y: Transform.posY[eid] + 1.2,
    z: Transform.posZ[eid],
    preset: 'sparkle',
    count: 6,
    duration: 0.5,
  });
}

export function isBlocking(): boolean {
  return blocking;
}

/** Called by abilities.ts after a dash: i-frames + perfect-dodge reward. */
export function notifyDodge(state: State, player: number): void {
  grantInvulnerability(player, DODGE_IFRAMES);
  if (perfectDodgeOpen(state, player)) {
    grantInvulnerability(player, PERFECT_DODGE_IFRAMES);
    playerStats.furyBonus = FURY_BONUS;
    playerStats.furyTimer = FURY_DURATION;
    // Slow-mo: the world crawls while the counter-window opens.
    hitStop(state, SLOWMO_DURATION, SLOWMO_SCALE);
    addCameraShake(0.25);
    playSound('levelup', { pitch: 1.3, originEid: player });
    spawnFloatingText(state, 'ESQUIVA PERFEITA!', {
      x: Transform.posX[player],
      y: Transform.posY[player] + 2.6,
      z: Transform.posZ[player],
      color: '#5ad0ff',
      duration: 1.1,
    });
    spawnParticleBurst(state, {
      x: Transform.posX[player],
      y: Transform.posY[player] + 1.0,
      z: Transform.posZ[player],
      preset: 'sparkle',
      count: 20,
      duration: 0.7,
    });
  }
}

/**
 * A dodge is "perfect" when some enemy is committed to an attack right now:
 * mid-windup (telegraph glow) within 4.5 m, or in attack stance within 3.2 m.
 */
const dodgeEnemyQuery = defineQuery([AiStateComponent, Health, Transform]);
function perfectDodgeOpen(state: State, player: number): boolean {
  const px = Transform.posX[player];
  const py = Transform.posY[player];
  const pz = Transform.posZ[player];
  for (const e of dodgeEnemyQuery(state.world)) {
    if (e === player || isDead(e)) continue;
    const dx = Transform.posX[e] - px;
    const dz = Transform.posZ[e] - pz;
    const dy = Transform.posY[e] - py;
    const d2 = dx * dx + dz * dz;
    if (Math.abs(dy) > 3.0) continue;
    const windup = AiStateComponent.lungePhase[e] === AI_LUNGE_PHASE_WINDUP;
    if (windup && d2 <= PERFECT_DODGE_RADIUS_SQ) return true;
    if (
      AiStateComponent.mode[e] === AI_MODE_ATTACK &&
      d2 <= PERFECT_DODGE_RADIUS_ATTACK_SQ
    )
      return true;
  }
  return false;
}

// ── Guard damage modifier (engine damage pipeline) ───────────────────────────
let uninstallModifier: (() => void) | null = null;
let installedForState: State | null = null;
let cachedPlayer = 0;

function playerEidOf(state: State): number {
  if (cachedPlayer && state.exists(cachedPlayer)) return cachedPlayer;
  cachedPlayer = state.getEntityByName('player') ?? 0;
  return cachedPlayer;
}

/**
 * Wire the guard into the engine damage pipeline (once per State). The
 * modifier runs BEFORE any HP is written: a parry returns 0 and the engine
 * skips the blow entirely — the hurt vignette, damage numbers and death
 * checks never see it.
 */
export function installGuardModifier(state: State): void {
  if (uninstallModifier && installedForState === state) return;
  uninstallModifier?.();
  uninstallModifier = registerDamageModifier((eid, amount, source) => {
    if (!blocking || eid !== playerEidOf(state)) return amount;
    // Facing (XZ) vs the direction the blow comes from — a guard doesn't
    // cover your back.
    const px = Transform.posX[eid];
    const pz = Transform.posZ[eid];
    const qx = WorldTransform.rotX[eid];
    const qy = WorldTransform.rotY[eid];
    const qz = WorldTransform.rotZ[eid];
    const qw = WorldTransform.rotW[eid];
    let fwx = 2 * (qx * qz + qw * qy);
    let fwz = 1 - 2 * (qx * qx + qy * qy);
    const len = Math.hypot(fwx, fwz) || 1;
    fwx /= len;
    fwz /= len;
    const dx = Transform.posX[source] - px;
    const dz = Transform.posZ[source] - pz;
    const dist = Math.hypot(dx, dz) || 1;
    const facing = (fwx * dx) / dist + (fwz * dz) / dist;
    if (source > 0 && facing < BLOCK_ARC_DOT) return amount; // hit from behind

    const parrying = time - blockStartTime <= PARRY_WINDOW;
    const midX = px + (dx / dist) * 0.8;
    const midY = Transform.posY[eid] + 1.2;
    const midZ = pz + (dz / dist) * 0.8;
    if (parrying) {
      const boss = source > 0 && isBossCreature(source);
      if (!boss && source > 0 && !isDead(source)) {
        staggerAi(state, source, PARRY_STAGGER);
        applyCctKnockback(
          state,
          source,
          dx / dist,
          dz / dist,
          PARRY_KNOCKBACK,
          0.2
        );
      }
      riposteTimer = RIPOSTE_WINDOW;
      hitStop(state, 0.12, 0.05);
      addCameraShake(0.35);
      playSound('shield-block', { originEid: eid, pitch: 1.35 });
      spawnFloatingText(state, boss ? 'GUARDADO!' : 'PARADO!', {
        x: px,
        y: midY + 1.1,
        z: pz,
        color: '#ffd24a',
        duration: 0.9,
      });
      spawnParticleBurst(state, {
        x: midX,
        y: midY,
        z: midZ,
        preset: 'sparks',
        count: 14,
        duration: 0.4,
      });
      return boss ? amount * PARRY_BOSS_DAMAGE_MULT : 0;
    }
    // Plain guard: chip damage, block SFX, sparks.
    playSound('shield-block', { originEid: eid, pitch: 0.85 });
    spawnParticleBurst(state, {
      x: midX,
      y: midY,
      z: midZ,
      preset: 'sparks',
      count: 6,
      duration: 0.3,
    });
    return amount * BLOCK_DAMAGE_MULT;
  });
  installedForState = state;
}

// ── HUD ──────────────────────────────────────────────────────────────────────
let comboEl: HTMLDivElement | null = null;
let comboNumEl: HTMLSpanElement | null = null;
let guardEl: HTMLDivElement | null = null;
let comboPopT = 0;

function buildHud(): void {
  if (comboEl || typeof document === 'undefined') return;
  const layer =
    document.querySelector('.vibe-hud-screen-layer') ?? document.body;
  comboEl = document.createElement('div');
  comboEl.style.cssText =
    'position:absolute;top:92px;left:50%;transform:translateX(-50%);z-index:12;' +
    'display:flex;flex-direction:column;align-items:center;pointer-events:none;' +
    'opacity:0;transition:opacity 0.3s;';
  comboNumEl = document.createElement('span');
  comboNumEl.style.cssText =
    'font:800 30px system-ui,sans-serif;color:#ffd24a;text-shadow:0 2px 6px #000;';
  comboNumEl.textContent = 'x0';
  const label = document.createElement('span');
  label.style.cssText =
    'font:700 11px system-ui,sans-serif;letter-spacing:3px;color:#fff;' +
    'text-shadow:0 1px 3px #000;opacity:0.85;';
  label.textContent = 'COMBO';
  comboEl.append(comboNumEl, label);
  layer.appendChild(comboEl);

  guardEl = document.createElement('div');
  guardEl.style.cssText =
    'position:absolute;bottom:150px;left:50%;transform:translateX(-50%);z-index:12;' +
    'padding:4px 14px;border-radius:999px;background:rgba(6,9,18,0.6);' +
    'border:1px solid #5ad0ff66;color:#bfe9ff;font:700 12px system-ui,sans-serif;' +
    'letter-spacing:2px;pointer-events:none;opacity:0;transition:opacity 0.15s;';
  guardEl.textContent = 'GUARDA [L]';
  layer.appendChild(guardEl);
}

function bumpComboHud(): void {
  updateComboHud();
  comboPopT = 0.12;
}

function updateComboHud(): void {
  if (!comboEl || !comboNumEl) return;
  comboNumEl.textContent = `x${comboHits}`;
  comboEl.style.opacity = comboHits > 0 ? '1' : '0';
}

// ── Per-frame update ─────────────────────────────────────────────────────────
export function updateCombatMechanics(
  state: State,
  player: number,
  dt: number
): void {
  buildHud();
  time += dt;

  if (chainTimer > 0) {
    chainTimer = Math.max(0, chainTimer - dt);
    if (chainTimer <= 0) chainStep = -1;
  }
  if (riposteTimer > 0) riposteTimer = Math.max(0, riposteTimer - dt);
  if (playerStats.furyTimer > 0) {
    playerStats.furyTimer = Math.max(0, playerStats.furyTimer - dt);
    if (playerStats.furyTimer <= 0) playerStats.furyBonus = 0;
  }
  if (comboTimer > 0) {
    comboTimer = Math.max(0, comboTimer - dt);
    if (comboTimer <= 0) notifyPlayerDamaged();
  }
  if (comboPopT > 0) {
    comboPopT = Math.max(0, comboPopT - dt);
    if (comboNumEl) {
      const k = comboPopT / 0.12;
      comboNumEl.style.transform = `scale(${1 + 0.35 * k})`;
    }
  }

  // Guard polling (engine input so it respects pause / the input map).
  if (!isGamePaused() && player > 0 && !isDead(player)) {
    const down = isKeyDown(BLOCK_KEY);
    if (down && !blockHeld) {
      blocking = true;
      blockStartTime = time;
      playerStats.blocking = true;
      if (guardEl) guardEl.style.opacity = '1';
    } else if (!down && blockHeld) {
      stopBlocking();
    }
    blockHeld = down;
  } else if (blocking) {
    stopBlocking();
  }

  void state;
}

function stopBlocking(): void {
  blocking = false;
  playerStats.blocking = false;
  if (guardEl) guardEl.style.opacity = '0';
}

/** HMR/teardown cleanup. */
export function clearCombatMechanics(): void {
  uninstallModifier?.();
  uninstallModifier = null;
  installedForState = null;
  stopBlocking();
  blockHeld = false;
  riposteTimer = 0;
  chainStep = -1;
  chainTimer = 0;
  comboHits = 0;
  comboTimer = 0;
  time = 0;
  cachedPlayer = 0;
  playerStats.furyBonus = 0;
  playerStats.furyTimer = 0;
  comboEl?.remove();
  guardEl?.remove();
  comboEl = null;
  comboNumEl = null;
  guardEl = null;
}
