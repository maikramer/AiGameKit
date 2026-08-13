import * as THREE from 'three';
import {
  loadGltfToSceneWithAnimator,
  playSound,
  spawnParticleBurst,
} from 'vibegame';
import type { MonoBehaviourContext } from 'vibegame';
import {
  Transform,
  isKeyDown,
  healHealth,
  registerInteractionTarget,
  unregisterInteractionTarget,
} from 'vibegame';
import { addGold } from '../game/economy.ts';
import { findPlayer } from '../game/player-query.ts';
import { showToast } from '../../../shared/src/ui';

// Treasure chest: static prop (model from the project's text3d pipeline) that
// drops gold + a heal once when the player walks up and presses F. Gold feeds
// the merchant shop loop. Commerce-focused — no inventory item, just currency.

const MODEL_URL = '/assets/meshes/village/treasure_chest_lod2.glb';
const OPEN_RANGE_SQ = 4.6 * 4.6;
const GOLD_REWARD = 60;
const HEAL_REWARD = 25;
const LID_OPEN_ANGLE = -0.9; // radians, tip the top back as it opens
const OPEN_ANIM_SECONDS = 0.4;

let group: THREE.Group | null = null;
let loadStarted = false;
let opened = false;
let openProgress = 0; // 0..1 lid-open animation
let glow = 0; // emissive flash, decays after opening
let fPressed = false;
const emissiveMats: THREE.MeshStandardMaterial[] = [];

export function start(ctx: MonoBehaviourContext): void {
  findPlayer(ctx.state);
  registerInteractionTarget(ctx.state, ctx.entity, {
    label: 'Abrir baú',
    key: 'F',
  });
  if (loadStarted) return;
  loadStarted = true;
  void loadGltfToSceneWithAnimator(ctx.state, MODEL_URL).then((result) => {
    group = result.group;
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
  const y = Transform.posY[eid];
  const z = Transform.posZ[eid];

  if (opened) {
    // Lid-open tween + a small lift, then the gold glow decays out.
    openProgress = Math.min(1, openProgress + dt / OPEN_ANIM_SECONDS);
    const ease = 1 - (1 - openProgress) * (1 - openProgress);
    group.position.set(x, y + 0.15 * ease, z);
    group.rotation.x = LID_OPEN_ANGLE * ease;
    if (glow > 0) {
      glow = Math.max(0, glow - dt * 1.5);
      for (const m of emissiveMats) m.emissiveIntensity = glow;
    }
    return;
  }

  group.position.set(x, y, z);

  const player = findPlayer(ctx.state);
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
    // Positional args are ignored by addGold (player-anchored) — and `gy`
    // never existed here, so passing it threw a ReferenceError on open.
    addGold(GOLD_REWARD);
    healHealth(player, HEAL_REWARD);
    playSound('coin');
    playSound('heal');
    spawnParticleBurst(ctx.state, {
      x,
      y: y + 0.6,
      z,
      preset: 'explosion',
      count: 22,
      duration: 0.9,
    });
    showToast(`Treasure! +${GOLD_REWARD} gold  ·  +${HEAL_REWARD} HP`, {
      color: '#ffe9a0',
      borderColor: '#ffd700',
      background: 'rgba(20,15,10,0.95)',
      font: '18px Georgia,serif',
      glow: '0 0 24px rgba(255,215,0,0.4)',
      durationMs: 1800,
    });
  }
  fPressed = f;
}
