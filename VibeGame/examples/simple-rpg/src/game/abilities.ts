// Active abilities with cooldowns, alongside the passive stat skills (Vitality/
// Strength/Agility in the pause-menu SkillsTab). Each is a keypress with a
// cooldown shown on a bottom-left ability bar:
//   [Q] Dash         — burst forward in your facing direction
//   [E] Heal         — instant self-heal
//   [R] Power Strike — radial damage burst around you
import {
  Health,
  Transform,
  WorldTransform,
  damageHealth,
  defineQuery,
  getBvhSurfaceHeight,
  getRapierWorld,
  getTerrainHeightAt,
  healHealth,
  isDead,
  isKeyDown,
  playSound,
  spawnFloatingText,
  spawnParticleBurst,
} from 'vibegame';
import type { State } from 'vibegame';
import * as RAPIER from '@dimforge/rapier3d-compat';
import { isGamePaused } from './pause';
import { heroStats } from './skills';

interface Ability {
  id: string;
  key: string;
  keyCode: string;
  icon: string;
  label: string;
  color: string;
  cooldown: number;
}

const ABILITIES: readonly Ability[] = [
  {
    id: 'dash',
    key: 'C',
    keyCode: 'KeyC',
    icon: '💨',
    label: 'Dash — burst forward',
    color: '#5ad0ff',
    cooldown: 3,
  },
  {
    id: 'heal',
    key: 'E',
    keyCode: 'KeyE',
    icon: '✚',
    label: 'Heal — restore HP',
    color: '#6ef07a',
    cooldown: 12,
  },
  {
    id: 'power',
    key: 'R',
    keyCode: 'KeyR',
    icon: '💥',
    label: 'Power Strike — radial damage',
    color: '#ffb24a',
    cooldown: 8,
  },
];

const HEAL_AMOUNT = 35;
const DASH_DISTANCE = 4.2;
const POWER_RADIUS = 4.8;
const POWER_DAMAGE = 60;
const POWER_VERTICAL = 3.0; // don't nuke high-flying mosquitoes from the ground
const TERRAIN_LAYER = 0x0001;

const cd: Record<string, number> = { dash: 0, heal: 0, power: 0 };
const pressed: Record<string, boolean> = {};
const healthQuery = defineQuery([Health, Transform]);

// ── HUD ability bar ────────────────────────────────────────────────────────
let barEl: HTMLDivElement | null = null;
const slotEls: Record<
  string,
  { cover: HTMLDivElement; secs: HTMLSpanElement; root: HTMLDivElement }
> = {};

function buildBar(): void {
  if (barEl || typeof document === 'undefined') return;
  const layer =
    document.querySelector('.vibe-hud-screen-layer') ?? document.body;
  barEl = document.createElement('div');
  barEl.style.cssText =
    'position:absolute;bottom:18px;left:18px;z-index:12;display:flex;gap:8px;pointer-events:none;';

  for (const a of ABILITIES) {
    const root = document.createElement('div');
    root.style.cssText =
      'position:relative;width:50px;height:50px;border-radius:11px;' +
      'display:flex;align-items:center;justify-content:center;font-size:23px;line-height:1;' +
      `border:1px solid ${a.color}66;` +
      'background:linear-gradient(135deg,rgba(14,18,34,0.78),rgba(10,14,26,0.66));' +
      'backdrop-filter:blur(10px);box-shadow:0 5px 18px rgba(0,0,0,0.3);pointer-events:auto;';
    root.textContent = a.icon;
    root.title = `[${a.key}] ${a.label} (cooldown ${a.cooldown}s)`;

    const keyBadge = document.createElement('span');
    keyBadge.textContent = a.key;
    keyBadge.style.cssText =
      'position:absolute;top:-7px;left:-7px;min-width:17px;height:17px;padding:0 4px;z-index:2;' +
      'border-radius:5px;background:#1b2238;color:#cfe;border:1px solid rgba(255,255,255,0.18);' +
      'font:800 11px system-ui,sans-serif;display:flex;align-items:center;justify-content:center;';

    // Cooldown sweep: a dark cover whose height shrinks from full → 0 as the
    // cooldown elapses, plus the remaining whole seconds.
    const cover = document.createElement('div');
    cover.style.cssText =
      'position:absolute;left:0;right:0;bottom:0;height:0%;z-index:1;border-radius:11px;' +
      'background:rgba(6,9,18,0.72);transition:height 0.08s linear;';
    const secs = document.createElement('span');
    secs.style.cssText =
      'position:absolute;inset:0;z-index:2;display:flex;align-items:center;justify-content:center;' +
      'font:800 16px system-ui,sans-serif;color:#fff;text-shadow:0 1px 3px #000;';

    root.append(cover, secs, keyBadge);
    barEl.appendChild(root);
    slotEls[a.id] = { cover, secs, root };
  }
  layer.appendChild(barEl);
}

function flash(id: string): void {
  const el = slotEls[id]?.root;
  if (!el) return;
  el.style.transform = 'scale(1.15)';
  setTimeout(() => el && (el.style.transform = 'scale(1)'), 110);
}

// ── Effects ──────────────────────────────────────────────────────────────
function heroForward(hero: number, out: { x: number; z: number }): void {
  // Local +Z axis of the hero rotation, projected to the ground plane.
  const x = WorldTransform.rotX[hero];
  const y = WorldTransform.rotY[hero];
  const z = WorldTransform.rotZ[hero];
  const w = WorldTransform.rotW[hero];
  let fx = 2 * (x * z + w * y);
  let fz = 1 - 2 * (x * x + y * y);
  const len = Math.hypot(fx, fz) || 1;
  fx /= len;
  fz /= len;
  out.x = fx;
  out.z = fz;
}

const _fwd = { x: 0, z: 0 };

/**
 * Clamp the dash so it doesn't teleport through walls/rocks: cast a ray forward
 * from chest height (offset past the hero's own capsule) and stop short of the
 * first solid hit. Falls back to the full distance when no physics world.
 */
function clampDashDistance(state: State, hero: number): number {
  const world = getRapierWorld(state);
  if (!world) return DASH_DISTANCE;
  const startOffset = 0.6; // clear the hero's own ~0.3 m capsule radius
  const origin = {
    x: Transform.posX[hero] + _fwd.x * startOffset,
    y: Transform.posY[hero] + 0.9,
    z: Transform.posZ[hero] + _fwd.z * startOffset,
  };
  const ray = new RAPIER.Ray(origin, { x: _fwd.x, y: 0, z: _fwd.z });
  const hit = world.castRay(
    ray,
    DASH_DISTANCE,
    true,
    RAPIER.QueryFilterFlags.EXCLUDE_SENSORS
  );
  if (!hit) return DASH_DISTANCE;
  return Math.max(0, startOffset + hit.timeOfImpact - 0.4);
}

function doDash(state: State, hero: number): void {
  heroForward(hero, _fwd);
  const dist = clampDashDistance(state, hero);
  const nx = Transform.posX[hero] + _fwd.x * dist;
  const nz = Transform.posZ[hero] + _fwd.z * dist;
  const gy =
    getBvhSurfaceHeight(state, nx, 500, nz, 2000, TERRAIN_LAYER) ??
    getTerrainHeightAt(state, nx, nz) ??
    Transform.posY[hero];
  // Move both the ECS transform and the kinematic body so the controller keeps it.
  Transform.posX[hero] = nx;
  Transform.posZ[hero] = nz;
  Transform.posY[hero] = gy;
  Transform.dirty[hero] = 1;
  const RB = state.getComponent('rigidbody');
  if (RB) {
    RB.posX[hero] = nx;
    RB.posZ[hero] = nz;
    RB.posY[hero] = gy;
  }
  spawnParticleBurst(state, {
    x: nx,
    y: gy + 0.5,
    z: nz,
    preset: 'dust',
    count: 16,
    duration: 0.5,
  });
  playSound('swing');
  flash('dash');
}

function doHeal(state: State, hero: number): void {
  healHealth(hero, HEAL_AMOUNT);
  playSound('heal');
  spawnFloatingText(state, `+${HEAL_AMOUNT}`, {
    x: Transform.posX[hero],
    y: Transform.posY[hero] + 2.0,
    z: Transform.posZ[hero],
    color: '#6ef07a',
    size: 0.6,
    duration: 1.0,
  });
  spawnParticleBurst(state, {
    x: Transform.posX[hero],
    y: Transform.posY[hero] + 1.0,
    z: Transform.posZ[hero],
    preset: 'sparkle',
    count: 18,
    duration: 0.8,
  });
  flash('heal');
}

function doPowerStrike(state: State, hero: number): void {
  const hx = Transform.posX[hero];
  const hy = Transform.posY[hero];
  const hz = Transform.posZ[hero];
  const merchant = state.getEntityByName('merchant');
  spawnParticleBurst(state, {
    x: hx,
    y: Transform.posY[hero] + 0.6,
    z: hz,
    preset: 'explosion',
    count: 30,
    duration: 0.7,
  });
  playSound('mine-break');
  const r2 = POWER_RADIUS * POWER_RADIUS;
  const damage = POWER_DAMAGE + heroStats.attackBonus;
  let hits = 0;
  for (const e of healthQuery(state.world)) {
    if (e === hero || e === merchant || isDead(e)) continue;
    const dx = Transform.posX[e] - hx;
    const dz = Transform.posZ[e] - hz;
    const dy = Transform.posY[e] - hy;
    if (dx * dx + dz * dz > r2 || Math.abs(dy) > POWER_VERTICAL) continue;
    damageHealth(e, damage);
    hits++;
  }
  if (hits > 0) {
    spawnFloatingText(state, '💥', {
      x: hx,
      y: Transform.posY[hero] + 1.6,
      z: hz,
      color: '#ffb24a',
      size: 0.8,
      duration: 0.7,
    });
  }
  flash('power');
}

function activate(state: State, hero: number, id: string): void {
  if (id === 'dash') doDash(state, hero);
  else if (id === 'heal') doHeal(state, hero);
  else if (id === 'power') doPowerStrike(state, hero);
}

/** Poll ability keys, tick cooldowns, update the bar. Call once/frame. */
export function updateAbilities(state: State, hero: number, dt: number): void {
  buildBar();

  for (const a of ABILITIES) {
    if (cd[a.id] > 0) cd[a.id] = Math.max(0, cd[a.id] - dt);
  }

  if (!isGamePaused() && hero > 0 && !isDead(hero)) {
    for (const a of ABILITIES) {
      const down = isKeyDown(a.keyCode);
      if (down && !pressed[a.keyCode] && cd[a.id] <= 0) {
        activate(state, hero, a.id);
        cd[a.id] = a.cooldown;
      }
      pressed[a.keyCode] = down;
    }
  }

  for (const a of ABILITIES) {
    const el = slotEls[a.id];
    if (!el) continue;
    const remain = cd[a.id];
    el.cover.style.height = `${(remain / a.cooldown) * 100}%`;
    el.secs.textContent = remain > 0 ? String(Math.ceil(remain)) : '';
    el.root.style.opacity = remain > 0 ? '0.85' : '1';
  }
}

/** HMR/teardown cleanup. */
export function clearAbilityBar(): void {
  barEl?.remove();
  barEl = null;
  for (const k of Object.keys(slotEls)) delete slotEls[k];
  for (const k of Object.keys(cd)) cd[k] = 0;
}
