// Real melee attack. The engine resolves damage only for projectiles (bombs)
// and enemy→hero AI, so the hero's [J] swing previously did nothing to enemies
// (it only played the swing clip + harvested trees/rocks via Destructible).
// This module makes [J] deal damage to enemies in a frontal arc, scaling with
// the resolved attack bonus (Strength ranks + merchant sword upgrades, folded
// into heroStats.attackBonus by HeroStatsSystem).
import {
  Health,
  Transform,
  WorldTransform,
  damageHealth,
  defineQuery,
  isDead,
  isKeyDown,
  playSound,
  spawnParticleBurst,
} from 'vibegame';
import type { State } from 'vibegame';
import { heroStats } from './skills';
import { isGamePaused } from './pause';

const BASE_MELEE_DAMAGE = 16;
const MELEE_RANGE = 3.0;
const MELEE_RANGE_SQ = MELEE_RANGE * MELEE_RANGE;
// Frontal cone: hit anything within ~75° of the facing direction.
const MELEE_ARC_DOT = Math.cos((75 * Math.PI) / 180);
// Vertical reach so the swing can't tag a high-flying mosquito from the ground.
const MELEE_VERTICAL = 2.5;
const SWING_COOLDOWN = 0.42;

const healthQuery = defineQuery([Health, Transform]);
const _fwd = { x: 0, z: 0 };
let swingTimer = 0;
let jPressed = false;

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

/**
 * Poll [J] and, on the press edge (rate-limited by a swing cooldown), damage
 * every living enemy inside the frontal melee cone. Call once per frame.
 */
export function updateMelee(state: State, hero: number, dt: number): void {
  if (swingTimer > 0) swingTimer = Math.max(0, swingTimer - dt);

  if (isGamePaused() || hero <= 0 || isDead(hero)) {
    jPressed = isKeyDown('KeyJ');
    return;
  }

  const down = isKeyDown('KeyJ');
  const edge = down && !jPressed;
  jPressed = down;
  if (!edge || swingTimer > 0) return;
  swingTimer = SWING_COOLDOWN;
  playSound('swing');

  const merchant = state.getEntityByName('merchant');
  heroForward(hero);
  const hx = Transform.posX[hero];
  const hy = Transform.posY[hero];
  const hz = Transform.posZ[hero];
  const dmg = BASE_MELEE_DAMAGE + heroStats.attackBonus;

  for (const e of healthQuery(state.world)) {
    if (e === hero || e === merchant || isDead(e)) continue;
    const dx = Transform.posX[e] - hx;
    const dz = Transform.posZ[e] - hz;
    const dy = Transform.posY[e] - hy;
    const d2 = dx * dx + dz * dz;
    if (d2 > MELEE_RANGE_SQ || Math.abs(dy) > MELEE_VERTICAL) continue;
    const dist = Math.sqrt(d2) || 1;
    if ((_fwd.x * dx + _fwd.z * dz) / dist < MELEE_ARC_DOT) continue;
    damageHealth(e, dmg);
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

/** HMR/teardown reset of the swing edge state. */
export function clearMelee(): void {
  swingTimer = 0;
  jPressed = false;
}
