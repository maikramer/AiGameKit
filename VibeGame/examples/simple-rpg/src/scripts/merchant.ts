import * as THREE from 'three';
import { defineQuery, isKeyDown, loadGltfToSceneWithAnimator } from 'vibegame';
import type { GltfAnimator, MonoBehaviourContext } from 'vibegame';
import { Transform, PlayerController } from 'vibegame';
import { getTerrainHeightAt } from '../../../../src/plugins/terrain/systems.ts';
import { getBvhSurfaceHeight } from '../../../../src/plugins/bvh/utils.ts';

const TALK_RANGE = 4.5;
const TURN_SPEED = 6; // rad/s — smooth turn toward the player
const TERRAIN_LAYER = 0x0001;
// Top of the hut floor box (local pos y 0.1 + half of size-y 0.2) above the
// terrain anchor; the merchant stands on the floor, not on the bare terrain.
const HUT_FLOOR_TOP = 0.2;
const MODEL_URL = '/assets/meshes/npc_merchant_rigged_animated.glb';
const IDLE_CLIP = 'Animator3D_BreatheIdle';

const playerQuery = defineQuery([PlayerController]);
let cachedPlayer = 0;

let group: THREE.Group | null = null;
let animator: GltfAnimator | null = null;
let footOffset = 0;
let yaw = 0; // faces the hut doorway (+Z) until the player approaches
let loadStarted = false;
const _box = new THREE.Box3();

let promptEl: HTMLDivElement | null = null;
let dialogEl: HTMLDivElement | null = null;
let dialogOpen = false;
let talkDebounce = false;
let cancelDebounce = false;

const LINES = [
  'Bem-vindo, viajante! / Welcome, traveler!',
  'Tenho poções e lâminas… / I have potions and blades…',
  'Volte quando tiver ouro. / Come back when you have gold.',
];
let lineIdx = 0;

function ensureDom(): void {
  if (promptEl) return;
  promptEl = document.createElement('div');
  promptEl.style.cssText =
    'position:fixed;bottom:90px;left:50%;transform:translateX(-50%);' +
    'background:rgba(8,12,28,0.82);color:#ffe08a;padding:8px 18px;border-radius:8px;' +
    'font:600 14px system-ui,Segoe UI,sans-serif;border:1px solid rgba(255,210,120,0.35);' +
    'box-shadow:0 6px 24px rgba(0,0,0,0.35);z-index:1500;pointer-events:none;display:none;';
  promptEl.innerHTML = 'Aperte <b>K</b> para falar com o mercador';

  dialogEl = document.createElement('div');
  dialogEl.style.cssText =
    'position:fixed;bottom:130px;left:50%;transform:translateX(-50%);max-width:min(520px,90vw);' +
    'background:rgba(12,16,34,0.92);color:#eef2fb;padding:16px 22px;border-radius:12px;' +
    'font:14px system-ui,Segoe UI,sans-serif;line-height:1.5;border:1px solid rgba(255,210,120,0.4);' +
    'box-shadow:0 10px 40px rgba(0,0,0,0.45);z-index:1501;pointer-events:none;display:none;';

  document.body.appendChild(promptEl);
  document.body.appendChild(dialogEl);
}

function findPlayer(ctx: MonoBehaviourContext): number {
  if (cachedPlayer && Transform.posX[cachedPlayer] !== undefined)
    return cachedPlayer;
  cachedPlayer = playerQuery(ctx.state.world)[0] ?? 0;
  return cachedPlayer;
}

function renderDialog(): void {
  if (!dialogEl) return;
  dialogEl.innerHTML =
    `<b style="color:#ffd27a">Mercador / Merchant</b><br/>${LINES[lineIdx]}` +
    `<br/><span style="opacity:0.6;font-size:12px">[K] continuar · [L] sair</span>`;
}

export function start(ctx: MonoBehaviourContext): void {
  ensureDom();
  findPlayer(ctx);

  if (!loadStarted) {
    loadStarted = true;
    void loadGltfToSceneWithAnimator(ctx.state, MODEL_URL, {
      crossfadeDuration: 0.3,
    }).then((result) => {
      group = result.group;
      animator = result.animator;
      _box.setFromObject(group);
      footOffset = Number.isFinite(_box.min.y) ? -_box.min.y : 0;
      animator?.play(IDLE_CLIP);
    });
  }
}

export function update(ctx: MonoBehaviourContext): void {
  const eid = ctx.entity;
  if (!group) return;

  animator?.update(ctx.deltaTime);

  const x = Transform.posX[eid];
  const z = Transform.posZ[eid];
  const gy =
    getBvhSurfaceHeight(ctx.state, x, 500, z, 2000, TERRAIN_LAYER) ??
    getTerrainHeightAt(ctx.state, x, z);

  const player = findPlayer(ctx);
  const dx = player ? Transform.posX[player] - x : 0;
  const dz = player ? Transform.posZ[player] - z : 0;
  const near = player !== 0 && dx * dx + dz * dz < TALK_RANGE * TALK_RANGE;

  // Turn smoothly toward the player while they're in talking range.
  const targetYaw = near ? Math.atan2(dx, dz) : 0;
  const err = Math.atan2(Math.sin(targetYaw - yaw), Math.cos(targetYaw - yaw));
  const maxTurn = TURN_SPEED * ctx.deltaTime;
  yaw += Math.min(maxTurn, Math.max(-maxTurn, err));

  group.position.set(x, gy + HUT_FLOOR_TOP + footOffset, z);
  group.rotation.set(0, yaw, 0);

  if (!promptEl) return;

  if (near) {
    promptEl.style.display = dialogOpen ? 'none' : 'block';
    if (isKeyDown('KeyK') && !talkDebounce) {
      talkDebounce = true;
      if (!dialogOpen) {
        dialogOpen = true;
        lineIdx = 0;
      } else {
        lineIdx = (lineIdx + 1) % LINES.length;
      }
      renderDialog();
      if (dialogEl) dialogEl.style.display = 'block';
    }
    if (!isKeyDown('KeyK')) talkDebounce = false;

    // L = cancelar/voltar: fecha o diálogo sem sair do alcance.
    if (isKeyDown('KeyL') && !cancelDebounce) {
      cancelDebounce = true;
      if (dialogOpen) {
        dialogOpen = false;
        if (dialogEl) dialogEl.style.display = 'none';
      }
    }
    if (!isKeyDown('KeyL')) cancelDebounce = false;
  } else {
    promptEl.style.display = 'none';
    if (dialogEl) dialogEl.style.display = 'none';
    dialogOpen = false;
  }
}
