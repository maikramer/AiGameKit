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
  playSoundAt,
  spawnFloatingText,
  spawnParticleBurst,
} from 'vibegame';
import type { State } from 'vibegame';
import * as RAPIER from '@dimforge/rapier3d-compat';
import { isGamePaused } from './pause';
import { createHudSlot } from './hud-slot';
import { playerStats } from './skills';

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
    icon: '/assets/icons/ability_dash.png',
    label: 'Investida — avanço rápido',
    color: '#5ad0ff',
    cooldown: 3,
  },
  {
    id: 'heal',
    key: 'E',
    keyCode: 'KeyE',
    icon: '/assets/icons/ability_heal.png',
    label: 'Cura — restaurar HP',
    color: '#6ef07a',
    cooldown: 12,
  },
  {
    id: 'power',
    key: 'R',
    keyCode: 'KeyR',
    icon: '/assets/icons/ability_power.png',
    label: 'Golpe Forte — dano em área',
    color: '#ffb24a',
    cooldown: 8,
  },
];

const HEAL_AMOUNT = 35;
const DASH_DISTANCE = 4.2;
const POWER_RADIUS = 4.8;
const POWER_DAMAGE = 60;
const POWER_VERTICAL = 3.0; // don't nuke high-flying enemies from the ground
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
    const { root, keyBadge } = createHudSlot({
      icon: a.icon,
      label: a.label,
      key: a.key,
      color: a.color,
      size: 50,
      iconFontSize: 23,
      iconImgSize: 38,
    });
    root.title = `[${a.key}] ${a.label} (cooldown ${a.cooldown}s)`;
    keyBadge.style.zIndex = '2';

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

    root.append(cover, secs);
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
function playerForward(player: number, out: { x: number; z: number }): void {
  // Local +Z axis of the player rotation, projected to the ground plane.
  const x = WorldTransform.rotX[player];
  const y = WorldTransform.rotY[player];
  const z = WorldTransform.rotZ[player];
  const w = WorldTransform.rotW[player];
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
 * from chest height (offset past the player's own capsule) and stop short of the
 * first solid hit. Falls back to the full distance when no physics world.
 */
function clampDashDistance(state: State, player: number): number {
  const world = getRapierWorld(state);
  if (!world) return DASH_DISTANCE;
  const startOffset = 0.6; // clear the player's own ~0.3 m capsule radius
  const origin = {
    x: Transform.posX[player] + _fwd.x * startOffset,
    y: Transform.posY[player] + 0.9,
    z: Transform.posZ[player] + _fwd.z * startOffset,
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

function doDash(state: State, player: number): void {
  playerForward(player, _fwd);
  const dist = clampDashDistance(state, player);
  const nx = Transform.posX[player] + _fwd.x * dist;
  const nz = Transform.posZ[player] + _fwd.z * dist;
  const gy =
    getBvhSurfaceHeight(state, nx, 500, nz, 4000, TERRAIN_LAYER) ??
    getTerrainHeightAt(state, nx, nz) ??
    Transform.posY[player];
  // Move both the ECS transform and the kinematic body so the controller keeps it.
  Transform.posX[player] = nx;
  Transform.posZ[player] = nz;
  Transform.posY[player] = gy;
  Transform.dirty[player] = 1;
  const RB = state.getComponent('rigidbody') as {
    posX: Float32Array;
    posY: Float32Array;
    posZ: Float32Array;
  } | null;
  if (RB) {
    RB.posX[player] = nx;
    RB.posZ[player] = nz;
    RB.posY[player] = gy;
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

function doHeal(state: State, player: number): void {
  healHealth(player, HEAL_AMOUNT);
  playSound('heal');
  spawnFloatingText(state, `+${HEAL_AMOUNT}`, {
    x: Transform.posX[player],
    y: Transform.posY[player] + 2.0,
    z: Transform.posZ[player],
    color: '#6ef07a',
    size: 0.6,
    duration: 1.0,
  });
  spawnParticleBurst(state, {
    x: Transform.posX[player],
    y: Transform.posY[player] + 1.0,
    z: Transform.posZ[player],
    preset: 'sparkle',
    count: 18,
    duration: 0.8,
  });
  flash('heal');
}

function doPowerStrike(state: State, player: number): void {
  const hx = Transform.posX[player];
  const hy = Transform.posY[player];
  const hz = Transform.posZ[player];
  const merchant = state.getEntityByName('merchant');
  spawnParticleBurst(state, {
    x: hx,
    y: Transform.posY[player] + 0.6,
    z: hz,
    preset: 'explosion',
    count: 30,
    duration: 0.7,
  });
  playSoundAt('mine-break', hx, hy, hz, { originEid: player });
  const r2 = POWER_RADIUS * POWER_RADIUS;
  const damage = POWER_DAMAGE + playerStats.attackBonus;
  let hits = 0;
  for (const e of healthQuery(state.world)) {
    if (e === player || e === merchant || isDead(e)) continue;
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
      y: Transform.posY[player] + 1.6,
      z: hz,
      color: '#ffb24a',
      size: 0.8,
      duration: 0.7,
    });
  }
  flash('power');
}

function activate(state: State, player: number, id: string): void {
  if (id === 'dash') doDash(state, player);
  else if (id === 'heal') doHeal(state, player);
  else if (id === 'power') doPowerStrike(state, player);
}

/** Poll ability keys, tick cooldowns, update the bar. Call once/frame. */
export function updateAbilities(state: State, player: number, dt: number): void {
  buildBar();

  for (const a of ABILITIES) {
    if (cd[a.id] > 0) cd[a.id] = Math.max(0, cd[a.id] - dt);
  }

  if (!isGamePaused() && player > 0 && !isDead(player)) {
    for (const a of ABILITIES) {
      const down = isKeyDown(a.keyCode);
      if (down && !pressed[a.keyCode] && cd[a.id] <= 0) {
        activate(state, player, a.id);
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
