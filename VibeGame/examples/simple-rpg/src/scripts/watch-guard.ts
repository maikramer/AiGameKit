// Watchtower guard: [F] survey — pins the four city gates on compass/minimap
// so a new player sees where the biomes start. Loads its own GLB + idle
// (merchant/healer pattern) because script= skips gltf-xml auto-idle.
import * as THREE from 'three';
import { loadGltfToSceneWithAnimator, playSound } from 'aigamekit-vibegame';
import type { GltfAnimator, MonoBehaviourContext } from 'aigamekit-vibegame';
import {
  Transform,
  isKeyDown,
  registerInteractionTarget,
  unregisterInteractionTarget,
  setWaypoint,
  clearWaypoints,
} from 'aigamekit-vibegame';
import { isGamePaused } from '../game/pause.ts';
import { findPlayer } from '../game/player-query.ts';
import { NpcIdleAnimator } from '../game/npc-anims.ts';
import { showToast } from '../../../shared/src/ui';
import {
  LOOKOUT_GATES,
  LOOKOUT_WAYPOINT_PREFIX,
} from '../game/city-amenities.ts';

const MODEL_URL = '/assets/meshes/characters/npc_guard_lod2.glb';
const IDLE_CLIP = 'idle';
// Guarda: conversa, abana a cabeça e acena entre rondas. O pack do guard
// (npc_guard) só embarca idle/lantern/no/talk/walk/yes — lean/foldarms
// pertencem a outros packs e o NpcIdleAnimator filtra o que não existe.
const idleVariety = new NpcIdleAnimator({
  idle: IDLE_CLIP,
  gestures: ['talk', 'no', 'yes'],
});
const TURN_SPEED = 6;
const TALK_RANGE_SQ = 4.5 * 4.5;
const FACE_RANGE_SQ = 5 * 5;

let group: THREE.Group | null = null;
let animator: GltfAnimator | null = null;
let yaw = 0;
let loadStarted = false;
let guardEid = 0;
let promptShown = false;
let fPressed = false;

function showPrompt(state: MonoBehaviourContext['state']): void {
  if (promptShown || !guardEid) return;
  registerInteractionTarget(state, guardEid, {
    label: 'Vigiar os portões',
    key: 'F',
  });
  promptShown = true;
}

function hidePrompt(state: MonoBehaviourContext['state']): void {
  if (!promptShown || !guardEid) return;
  unregisterInteractionTarget(state, guardEid);
  promptShown = false;
}

function pinGates(state: MonoBehaviourContext['state']): void {
  clearWaypoints(state, LOOKOUT_WAYPOINT_PREFIX);
  const py = group ? group.position.y : 0;
  for (const gate of LOOKOUT_GATES) {
    setWaypoint(state, {
      id: gate.id,
      x: gate.x,
      y: py,
      z: gate.z,
      kind: 'poi',
      label: gate.label,
      color: gate.color,
      glyph: '▲',
    });
  }
  playSound('shop-open');
  showToast('A guarda aponta os quatro portões. Olha a bússola.', {
    color: '#c8d8a0',
    borderColor: '#6a8a40',
    background: 'rgba(12,16,8,0.95)',
    durationMs: 2800,
  });
}

export function start(ctx: MonoBehaviourContext): void {
  findPlayer(ctx.state);
  guardEid = ctx.entity;
  showPrompt(ctx.state);
  if (loadStarted) return;
  loadStarted = true;
  void loadGltfToSceneWithAnimator(ctx.state, MODEL_URL, {
    crossfadeDuration: 0.3,
  }).then((result) => {
    group = result.group;
    animator = result.animator;
    animator?.play(IDLE_CLIP);
    idleVariety.start(animator);
  });
}

export function update(ctx: MonoBehaviourContext): void {
  if (isGamePaused()) return;
  if (!group) return;
  animator?.update(ctx.deltaTime);
  idleVariety.update(ctx.deltaTime, animator);

  const eid = ctx.entity;
  const x = Transform.posX[eid];
  const y = Transform.posY[eid];
  const z = Transform.posZ[eid];

  const player = findPlayer(ctx.state);
  const dx = player ? Transform.posX[player] - x : 0;
  const dz = player ? Transform.posZ[player] - z : 0;
  const distSq = dx * dx + dz * dz;

  const near = player !== 0 && distSq < FACE_RANGE_SQ;
  const targetYaw = near ? Math.atan2(dx, dz) : 0;
  const err = Math.atan2(Math.sin(targetYaw - yaw), Math.cos(targetYaw - yaw));
  const maxTurn = TURN_SPEED * ctx.deltaTime;
  yaw += Math.min(maxTurn, Math.max(-maxTurn, err));
  group.position.set(x, y, z);
  group.rotation.set(0, yaw, 0);

  if (player && distSq < TALK_RANGE_SQ) showPrompt(ctx.state);
  else hidePrompt(ctx.state);

  const f = isKeyDown('KeyF');
  if (f && !fPressed && player && distSq < TALK_RANGE_SQ) {
    pinGates(ctx.state);
  }
  fPressed = f;
}
