import * as THREE from 'three';
import {
  defineQuery,
  loadGltfToSceneWithAnimator,
  playSound,
  spawnParticleBurst,
} from 'vibegame';
import type { MonoBehaviourContext } from 'vibegame';
import {
  Transform,
  PlayerController,
  getTerrainHeightAt,
  getBvhSurfaceHeight,
  isKeyDown,
  healHealth,
  registerInteractionTarget,
  unregisterInteractionTarget,
} from 'vibegame';
import { addGold } from '../game/economy.ts';

// Treasure chest: static prop (model from the project's text3d pipeline) that
// drops gold + a heal once when the player walks up and presses F. Gold feeds
// the merchant shop loop. Commerce-focused — no inventory item, just currency.

const MODEL_URL = '/assets/meshes/treasure_chest_lod0.glb';
const TERRAIN_LAYER = 0x0001;
const OPEN_RANGE_SQ = 4.6 * 4.6;
const GOLD_REWARD = 60;
const HEAL_REWARD = 25;
const LID_OPEN_ANGLE = -0.9; // radians, tip the top back as it opens
const OPEN_ANIM_SECONDS = 0.4;

const playerQuery = defineQuery([PlayerController]);
let cachedPlayer = 0;

let group: THREE.Group | null = null;
let footOffset = 0;
let baseY = 0;
let loadStarted = false;
let opened = false;
let openProgress = 0; // 0..1 lid-open animation
let glow = 0; // emissive flash, decays after opening
let fPressed = false;
let toast: HTMLDivElement | null = null;
let toastTimeout: ReturnType<typeof setTimeout> | null = null;
const _box = new THREE.Box3();
const emissiveMats: THREE.MeshStandardMaterial[] = [];

function findPlayer(ctx: MonoBehaviourContext): number {
  if (cachedPlayer && Transform.posX[cachedPlayer] !== undefined)
    return cachedPlayer;
  cachedPlayer = playerQuery(ctx.state.world)[0] ?? 0;
  return cachedPlayer;
}

function showToast(message: string): void {
  if (!toast) {
    toast = document.createElement('div');
    toast.style.cssText =
      'position:fixed;top:18%;left:50%;transform:translateX(-50%);' +
      'background:rgba(20,15,10,0.95);border:2px solid #ffd700;border-radius:8px;' +
      'padding:12px 22px;z-index:1000;font:18px Georgia,serif;color:#ffe9a0;' +
      'box-shadow:0 0 24px rgba(255,215,0,0.4);opacity:0;transition:opacity 0.2s;';
    document.body.appendChild(toast);
  }
  toast.textContent = message;
  toast.style.opacity = '1';
  if (toastTimeout) clearTimeout(toastTimeout);
  toastTimeout = setTimeout(() => {
    if (toast) toast.style.opacity = '0';
  }, 1800);
}

export function start(ctx: MonoBehaviourContext): void {
  findPlayer(ctx);
  registerInteractionTarget(ctx.state, ctx.entity, {
    label: 'Open chest',
    key: 'F',
  });
  if (loadStarted) return;
  loadStarted = true;
  void loadGltfToSceneWithAnimator(ctx.state, MODEL_URL).then((result) => {
    group = result.group;
    _box.setFromObject(group);
    footOffset = Number.isFinite(_box.min.y) ? -_box.min.y : 0;
    group.traverse((o) => {
      const mesh = o as THREE.Mesh;
      if (!mesh.isMesh) return;
      const mat = mesh.material as THREE.MeshStandardMaterial;
      if (mat && 'emissiveIntensity' in mat) {
        mat.emissive = new THREE.Color(0xffd24a);
        mat.emissiveIntensity = 0;
        emissiveMats.push(mat);
      }
    });
  });
}

export function update(ctx: MonoBehaviourContext): void {
  if (!group) return;
  const eid = ctx.entity;
  const dt = ctx.deltaTime;

  const x = Transform.posX[eid];
  const z = Transform.posZ[eid];
  const gy =
    getBvhSurfaceHeight(ctx.state, x, 500, z, 2000, TERRAIN_LAYER) ??
    getTerrainHeightAt(ctx.state, x, z);
  baseY = gy + footOffset;

  if (opened) {
    // Lid-open tween + a small lift, then the gold glow decays out.
    openProgress = Math.min(1, openProgress + dt / OPEN_ANIM_SECONDS);
    const ease = 1 - (1 - openProgress) * (1 - openProgress);
    group.position.set(x, baseY + 0.15 * ease, z);
    group.rotation.x = LID_OPEN_ANGLE * ease;
    if (glow > 0) {
      glow = Math.max(0, glow - dt * 1.5);
      for (const m of emissiveMats) m.emissiveIntensity = glow;
    }
    return;
  }

  group.position.set(x, baseY, z);

  const player = findPlayer(ctx);
  if (!player) return;
  const dx = Transform.posX[player] - x;
  const dz = Transform.posZ[player] - z;
  const near = dx * dx + dz * dz < OPEN_RANGE_SQ;

  const f = isKeyDown('KeyF');
  if (near && f && !fPressed) {
    opened = true;
    openProgress = 0;
    glow = 1.6;
    unregisterInteractionTarget(ctx.state, eid);
    addGold(GOLD_REWARD, x, gy, z);
    healHealth(player, HEAL_REWARD);
    playSound('coin');
    playSound('heal');
    spawnParticleBurst(ctx.state, {
      x,
      y: baseY + 0.6,
      z,
      preset: 'explosion',
      count: 22,
      duration: 0.9,
    });
    showToast(`Treasure! +${GOLD_REWARD} gold  ·  +${HEAL_REWARD} HP`);
  }
  fPressed = f;
}
