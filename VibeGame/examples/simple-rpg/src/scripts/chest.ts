import * as THREE from 'three';
import { defineQuery, loadGltfToSceneWithAnimator, playSound } from 'vibegame';
import type { MonoBehaviourContext } from 'vibegame';
import {
  Transform,
  PlayerController,
  getTerrainHeightAt,
  getBvhSurfaceHeight,
  isKeyDown,
  healHealth,
} from 'vibegame';
import { addGold } from '../game/economy.ts';

// Treasure chest: static prop (model from the project's text3d pipeline) that
// drops gold + a heal once when the player walks up and presses F. Gold feeds
// the merchant shop loop. Commerce-focused — no inventory item, just currency.

const MODEL_URL = '/assets/meshes/treasure_chest_lod0.glb';
const TERRAIN_LAYER = 0x0001;
const OPEN_RANGE_SQ = 3.2 * 3.2;
const GOLD_REWARD = 60;
const HEAL_REWARD = 25;

const playerQuery = defineQuery([PlayerController]);
let cachedPlayer = 0;

let group: THREE.Group | null = null;
let footOffset = 0;
let loadStarted = false;
let opened = false;
let fPressed = false;
let toast: HTMLDivElement | null = null;
let toastTimeout: ReturnType<typeof setTimeout> | null = null;
const _box = new THREE.Box3();

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
  if (loadStarted) return;
  loadStarted = true;
  void loadGltfToSceneWithAnimator(ctx.state, MODEL_URL).then((result) => {
    group = result.group;
    _box.setFromObject(group);
    footOffset = Number.isFinite(_box.min.y) ? -_box.min.y : 0;
  });
}

export function update(ctx: MonoBehaviourContext): void {
  if (!group) return;
  const eid = ctx.entity;

  const x = Transform.posX[eid];
  const z = Transform.posZ[eid];
  const gy =
    getBvhSurfaceHeight(ctx.state, x, 500, z, 2000, TERRAIN_LAYER) ??
    getTerrainHeightAt(ctx.state, x, z);
  group.position.set(x, gy + footOffset, z);

  if (opened) return;

  const player = findPlayer(ctx);
  if (!player) return;
  const dx = Transform.posX[player] - x;
  const dz = Transform.posZ[player] - z;
  const near = dx * dx + dz * dz < OPEN_RANGE_SQ;

  const f = isKeyDown('KeyF');
  if (near && f && !fPressed) {
    opened = true;
    addGold(GOLD_REWARD, x, gy, z);
    healHealth(player, HEAL_REWARD);
    playSound('coin');
    playSound('heal');
    // Sink the lid slightly so the chest visibly reads as looted.
    group.rotation.set(-0.35, group.rotation.y, 0);
    showToast(`Treasure! +${GOLD_REWARD} gold  ·  +${HEAL_REWARD} HP`);
  }
  fPressed = f;
}
