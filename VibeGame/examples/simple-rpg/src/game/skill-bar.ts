// Skill row [Y][U][I][O] — weapon techniques on their own cooldown bar, above
// the ability bar. The heavy axe clip (slow, committed, huge arc) became the
// Y skill instead of a normal swing; the normal axe swing shares the sword
// combo pool at its own pace (see ATTACK_POOLS in main.ts).
//   [Y] Machado Brutal — the heavy axe sequence: slow, wide frontal cleave
//   [U] Grito de Guerra — temp attack buff
//   [I] Redemoinho — 360° sword sweep
//   [O] Perfuração — spear dash-stab (dash, then pierce everything on the line)
import {
  addCameraShake,
  applyCctKnockback,
  createHudSlot,
  damageHealth,
  defineQuery,
  getAnimator,
  getBvhSurfaceHeight,
  getRapierWorld,
  hitStop,
  Health,
  isDead,
  isKeyDown,
  playSound,
  playSoundAt,
  PlayerGltfConfig,
  spawnFloatingText,
  spawnParticleBurst,
  staggerAi,
  Transform,
  WorldTransform,
} from 'vibegame';
import type { State } from 'vibegame';
import * as RAPIER from '@dimforge/rapier3d-compat';
import { isGamePaused } from './pause';
import { playerAttackPower, playerStats } from './skills';
import { isBossCreature } from '../scripts/creature';
import { notePlayerAttacked } from './melee';
import {
  comboDamageMult,
  executeMultiplier,
  notifyPlayerHitLanded,
} from './combat-mechanics';

interface SkillDef {
  id: string;
  key: string;
  keyCode: string;
  icon: string;
  label: string;
  color: string;
  cooldown: number;
}

const SKILLS: readonly SkillDef[] = [
  {
    id: 'heavy',
    key: 'Y',
    keyCode: 'KeyY',
    icon: '/assets/icons/skill_heavy.png',
    label: 'Machado Brutal — cleave frontal pesado',
    color: '#ff8a33',
    cooldown: 8,
  },
  {
    id: 'warcry',
    key: 'U',
    keyCode: 'KeyU',
    icon: '/assets/icons/skill_warcry.png',
    label: 'Grito de Guerra — +6 ataque por 10 s',
    color: '#ffd24a',
    cooldown: 18,
  },
  {
    id: 'whirl',
    key: 'I',
    keyCode: 'KeyI',
    icon: '/assets/icons/skill_whirl.png',
    label: 'Redemoinho — golpe circular',
    color: '#5ad0ff',
    cooldown: 6,
  },
  {
    id: 'thrust',
    key: 'O',
    keyCode: 'KeyO',
    icon: '/assets/icons/skill_thrust.png',
    label: 'Perfuração — avanço com lança',
    color: '#b18cff',
    cooldown: 5,
  },
];

// ── Tuning ───────────────────────────────────────────────────────────────────
const HEAVY_DAMAGE = 42;
const HEAVY_RANGE = 3.2;
const HEAVY_ARC_DOT = Math.cos((60 * Math.PI) / 180); // 120° frontal cleave
const HEAVY_KNOCKBACK = 1.6;
const HEAVY_CLIP_SCALE = 0.85; // slow, committed — the "heavy skill" read
const HEAVY_IMPACT_FRACTION = 0.45; // lands late in the long wind-up

const WARCRY_BONUS = 6;
const WARCRY_DURATION = 10;

const WHIRL_DAMAGE = 26;
const WHIRL_RADIUS = 3.0;
const WHIRL_KNOCKBACK = 0.6;
const WHIRL_CLIP_SCALE = 1.55; // fast, light — opposite of the heavy read
const WHIRL_IMPACT_FRACTION = 0.35;

const THRUST_DAMAGE = 34;
const THRUST_RANGE = 4.0;
const THRUST_ARC_DOT = Math.cos((20 * Math.PI) / 180); // narrow pierce line
const THRUST_KNOCKBACK = 1.0;
const THRUST_DASH = 2.2;
const THRUST_CLIP_SCALE = 1.2;
const THRUST_IMPACT_FRACTION = 0.4;

const VERTICAL = 3.0;
const TERRAIN_LAYER = 0x0001;
const PITCH_JITTER = 0.08;

// ── Shared strike helper (cone/circle AoE with the combat-feel stack) ────────

interface StrikeOpts {
  radius: number;
  /** Min cos(angle to facing) — use -1 for a full circle. */
  arcDot: number;
  damage: number;
  knockback: number;
  hitStopSec: number;
  shake: number;
}

const healthQuery = defineQuery([Health, Transform]);
const _fwd = { x: 0, z: 0 };

function playerForward(player: number, out: { x: number; z: number }): void {
  const x = WorldTransform.rotX[player];
  const y = WorldTransform.rotY[player];
  const z = WorldTransform.rotZ[player];
  const w = WorldTransform.rotW[player];
  let fx = 2 * (x * z + w * y);
  let fz = 1 - 2 * (x * x + y * y);
  const len = Math.hypot(fx, fz) || 1;
  out.x = fx / len;
  out.z = fz / len;
}

function strike(
  state: State,
  player: number,
  aimX: number,
  aimZ: number,
  o: StrikeOpts
): number {
  const hx = Transform.posX[player];
  const hy = Transform.posY[player];
  const hz = Transform.posZ[player];
  const merchant = state.getEntityByName('merchant');
  const r2 = o.radius * o.radius;
  const comboMult = comboDamageMult();
  let hits = 0;
  for (const e of healthQuery(state.world)) {
    if (e === player || e === merchant || isDead(e)) continue;
    const dx = Transform.posX[e] - hx;
    const dz = Transform.posZ[e] - hz;
    const dy = Transform.posY[e] - hy;
    if (dx * dx + dz * dz > r2 || Math.abs(dy) > VERTICAL) continue;
    const dist = Math.hypot(dx, dz) || 1;
    const ux = dx / dist;
    const uz = dz / dist;
    if (o.arcDot > -1 && aimX * ux + aimZ * uz < o.arcDot) continue;
    hits++;
    let dmg = o.damage * comboMult;
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
    damageHealth(e, Math.round(dmg), player);
    if (!isBossCreature(e)) {
      const kb = Math.max(0.4, o.knockback);
      applyCctKnockback(state, e, ux, uz, kb, 0.22);
      if (!isDead(e)) staggerAi(state, e, 0.35);
    }
    spawnParticleBurst(state, {
      x: Transform.posX[e],
      y: Transform.posY[e] + 1.0,
      z: Transform.posZ[e],
      preset: 'sparks',
      count: 10,
      duration: 0.4,
    });
  }
  if (hits > 0) {
    hitStop(state, o.hitStopSec, 0.05);
    addCameraShake(o.shake);
    notifyPlayerHitLanded(hits);
  }
  return hits;
}

// ── Swing scheduling (impact at a fraction of the played clip) ───────────────

interface PendingSkill {
  delay: number;
  fire: (state: State, player: number) => void;
}
let pending: PendingSkill | null = null;

function findClipDuration(
  state: State,
  player: number,
  hint: string
): {
  animator: NonNullable<ReturnType<typeof getAnimator>>;
  name: string;
  duration: number;
} | null {
  if (!state.hasComponent(player, PlayerGltfConfig)) return null;
  const regIdx = PlayerGltfConfig.animatorRegistryIndex[player];
  const animator = regIdx ? getAnimator(state, regIdx) : undefined;
  if (!animator) return null;
  const lower = hint.toLowerCase();
  const exact = animator.clipNames.find((n) => n.toLowerCase() === lower);
  const name =
    exact ?? animator.clipNames.find((n) => n.toLowerCase().includes(lower));
  if (!name) return null;
  const duration = animator.clips.get(name)?.duration ?? 0;
  return duration > 0 ? { animator, name, duration } : null;
}

/** Play the skill's clip and schedule the effect at its impact frame. */
function castSwing(
  state: State,
  player: number,
  clipHint: string,
  timeScale: number,
  impactFraction: number,
  fire: (state: State, player: number) => void
): boolean {
  const clip = findClipDuration(state, player, clipHint);
  if (clip) {
    clip.animator.playOverride(clip.name, { loop: false, timeScale });
    pending = { delay: (clip.duration * impactFraction) / timeScale, fire };
    return true;
  }
  // No clip rig: fire on a fixed short wind-up so the skill still works.
  pending = { delay: 0.25, fire };
  return false;
}

// ── Skill effects ────────────────────────────────────────────────────────────

function doHeavy(state: State, player: number): void {
  playerForward(player, _fwd);
  playSound('swing', {
    originEid: player,
    pitch: 0.8 + (Math.random() * 2 - 1) * PITCH_JITTER,
  });
  const hits = strike(state, player, _fwd.x, _fwd.z, {
    radius: HEAVY_RANGE,
    arcDot: HEAVY_ARC_DOT,
    damage: HEAVY_DAMAGE + playerAttackPower(),
    knockback: HEAVY_KNOCKBACK,
    hitStopSec: 0.14,
    shake: 0.55,
  });
  if (hits > 0) {
    playSoundAt(
      'mine-break',
      Transform.posX[player],
      Transform.posY[player],
      Transform.posZ[player],
      {
        originEid: player,
        pitch: 0.9,
      }
    );
    spawnParticleBurst(state, {
      x: Transform.posX[player] + _fwd.x * 1.4,
      y: Transform.posY[player] + 0.8,
      z: Transform.posZ[player] + _fwd.z * 1.4,
      preset: 'explosion',
      count: 22,
      duration: 0.6,
    });
  }
}

function doWarcry(state: State, player: number): void {
  playerStats.buffAttackBonus = WARCRY_BONUS;
  playerStats.buffAttackTimer = WARCRY_DURATION;
  playSound('levelup', { pitch: 0.95 });
  spawnFloatingText(state, `FÚRIA +${WARCRY_BONUS}!`, {
    x: Transform.posX[player],
    y: Transform.posY[player] + 2.4,
    z: Transform.posZ[player],
    color: '#ffd24a',
    duration: 1.1,
  });
  spawnParticleBurst(state, {
    x: Transform.posX[player],
    y: Transform.posY[player] + 1.2,
    z: Transform.posZ[player],
    preset: 'magic',
    count: 20,
    duration: 0.8,
  });
}

function doWhirl(state: State, player: number): void {
  playSound('swing', {
    originEid: player,
    pitch: 1.25 + (Math.random() * 2 - 1) * PITCH_JITTER,
  });
  strike(state, player, 0, 0, {
    radius: WHIRL_RADIUS,
    arcDot: -1, // full circle
    damage: WHIRL_DAMAGE + playerAttackPower(),
    knockback: WHIRL_KNOCKBACK,
    hitStopSec: 0.09,
    shake: 0.4,
  });
  spawnParticleBurst(state, {
    x: Transform.posX[player],
    y: Transform.posY[player] + 1.0,
    z: Transform.posZ[player],
    preset: 'slash',
    count: 2,
    duration: 0.25,
  });
}

/** Ray-clamped dash, mirroring abilities.ts doDash. */
function thrustDash(
  state: State,
  player: number,
  fwdX: number,
  fwdZ: number
): void {
  const world = getRapierWorld(state);
  let dist = THRUST_DASH;
  if (world) {
    const origin = {
      x: Transform.posX[player] + fwdX * 0.6,
      y: Transform.posY[player] + 0.9,
      z: Transform.posZ[player] + fwdZ * 0.6,
    };
    const ray = new RAPIER.Ray(origin, { x: fwdX, y: 0, z: fwdZ });
    const hit = world.castRay(
      ray,
      THRUST_DASH,
      true,
      RAPIER.QueryFilterFlags.EXCLUDE_SENSORS
    );
    if (hit) dist = Math.max(0, 0.6 + hit.timeOfImpact - 0.4);
  }
  const nx = Transform.posX[player] + fwdX * dist;
  const nz = Transform.posZ[player] + fwdZ * dist;
  const rawGy =
    getBvhSurfaceHeight(state, nx, 500, nz, 4000, TERRAIN_LAYER) ??
    Transform.posY[player];
  const gy = Number.isFinite(rawGy)
    ? (rawGy as number)
    : Transform.posY[player];
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
    y: gy + 0.4,
    z: nz,
    preset: 'dust',
    count: 10,
    duration: 0.4,
  });
}

function doThrust(state: State, player: number): void {
  playerForward(player, _fwd);
  thrustDash(state, player, _fwd.x, _fwd.z);
  playSound('swing', {
    originEid: player,
    pitch: 1.05 + (Math.random() * 2 - 1) * PITCH_JITTER,
  });
  const hits = strike(state, player, _fwd.x, _fwd.z, {
    radius: THRUST_RANGE,
    arcDot: THRUST_ARC_DOT,
    damage: THRUST_DAMAGE + playerAttackPower(),
    knockback: THRUST_KNOCKBACK,
    hitStopSec: 0.1,
    shake: 0.35,
  });
  if (hits > 0) {
    spawnParticleBurst(state, {
      x: Transform.posX[player] + _fwd.x * 2.0,
      y: Transform.posY[player] + 1.0,
      z: Transform.posZ[player] + _fwd.z * 2.0,
      preset: 'slash',
      count: 1,
      duration: 0.25,
    });
  }
}

// ── HUD ──────────────────────────────────────────────────────────────────────

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
    'position:absolute;bottom:78px;left:18px;z-index:12;display:flex;gap:8px;pointer-events:none;';
  for (const s of SKILLS) {
    const { root, keyBadge } = createHudSlot({
      icon: s.icon,
      label: s.label,
      key: s.key,
      color: s.color,
      size: 46,
      iconFontSize: 21,
      iconImgSize: 34,
    });
    root.title = `[${s.key}] ${s.label} (cooldown ${s.cooldown}s)`;
    keyBadge.style.zIndex = '2';
    const cover = document.createElement('div');
    cover.style.cssText =
      'position:absolute;left:0;right:0;bottom:0;height:0%;z-index:1;border-radius:11px;' +
      'background:rgba(6,9,18,0.72);transition:height 0.08s linear;';
    const secs = document.createElement('span');
    secs.style.cssText =
      'position:absolute;inset:0;z-index:2;display:flex;align-items:center;justify-content:center;' +
      'font:800 14px system-ui,sans-serif;color:#fff;text-shadow:0 1px 3px #000;';
    root.append(cover, secs);
    barEl.appendChild(root);
    slotEls[s.id] = { cover, secs, root };
  }
  layer.appendChild(barEl);
}

function flash(id: string): void {
  const el = slotEls[id]?.root;
  if (!el) return;
  el.style.transform = 'scale(1.15)';
  setTimeout(() => el && (el.style.transform = 'scale(1)'), 110);
}

// ── Update loop ──────────────────────────────────────────────────────────────

const cd: Record<string, number> = { heavy: 0, warcry: 0, whirl: 0, thrust: 0 };
const pressed: Record<string, boolean> = {};

export function updateSkillBar(state: State, player: number, dt: number): void {
  buildBar();

  for (const s of SKILLS) {
    if (cd[s.id] > 0) cd[s.id] = Math.max(0, cd[s.id] - dt);
  }

  // War-cry buff countdown (attack bonus applied by PlayerStatsSystem).
  if (playerStats.buffAttackTimer > 0) {
    playerStats.buffAttackTimer = Math.max(0, playerStats.buffAttackTimer - dt);
    if (playerStats.buffAttackTimer <= 0) playerStats.buffAttackBonus = 0;
  }

  // Pending skill impact (scheduled at the clip's impact frame).
  if (pending) {
    pending.delay -= dt;
    if (pending.delay <= 0) {
      const p = pending;
      pending = null;
      if (!isGamePaused() && player > 0 && !isDead(player)) {
        p.fire(state, player);
      }
    }
  }

  if (!isGamePaused() && player > 0 && !isDead(player)) {
    for (const s of SKILLS) {
      const down = isKeyDown(s.keyCode);
      if (down && !pressed[s.keyCode] && cd[s.id] <= 0) {
        cast(s, state, player);
      }
      pressed[s.keyCode] = down;
    }
  }

  for (const s of SKILLS) {
    const el = slotEls[s.id];
    if (!el) continue;
    const remain = cd[s.id];
    el.cover.style.height = `${(remain / s.cooldown) * 100}%`;
    el.secs.textContent = remain > 0 ? String(Math.ceil(remain)) : '';
    el.root.style.opacity = remain > 0 ? '0.85' : '1';
    el.root.style.outline =
      s.id === 'warcry' && playerStats.buffAttackTimer > 0
        ? '2px solid #ffd24a'
        : 'none';
  }
}

function cast(s: SkillDef, state: State, player: number): void {
  cd[s.id] = s.cooldown;
  flash(s.id);
  // Skills are attacks too — they keep the guard stance alive.
  notePlayerAttacked();
  if (s.id === 'warcry') {
    doWarcry(state, player);
    return;
  }
  if (s.id === 'heavy') {
    castSwing(
      state,
      player,
      'axe',
      HEAVY_CLIP_SCALE,
      HEAVY_IMPACT_FRACTION,
      doHeavy
    );
    return;
  }
  if (s.id === 'whirl') {
    castSwing(
      state,
      player,
      'sword',
      WHIRL_CLIP_SCALE,
      WHIRL_IMPACT_FRACTION,
      doWhirl
    );
    return;
  }
  castSwing(
    state,
    player,
    'spear',
    THRUST_CLIP_SCALE,
    THRUST_IMPACT_FRACTION,
    doThrust
  );
}

/** HMR/teardown cleanup. */
export function clearSkillBar(): void {
  barEl?.remove();
  barEl = null;
  for (const k of Object.keys(slotEls)) delete slotEls[k];
  for (const k of Object.keys(cd)) cd[k] = 0;
  for (const k of Object.keys(pressed)) delete pressed[k];
  pending = null;
  playerStats.buffAttackBonus = 0;
  playerStats.buffAttackTimer = 0;
}
