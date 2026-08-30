// Chapel healer: walk up, press F, pay a modest fee, leave at full HP.
// Loads its own GLB + idle (same pattern as the merchant) so the entity can
// keep a MonoBehaviour without freezing in T-pose.
import * as THREE from 'three';
import { loadGltfToSceneWithAnimator, playSound } from 'aigamekit-vibegame';
import type { GltfAnimator, MonoBehaviourContext } from 'aigamekit-vibegame';
import {
  Transform,
  isKeyDown,
  Health,
  healHealth,
  registerInteractionTarget,
  unregisterInteractionTarget,
} from 'aigamekit-vibegame';
import { spendGold } from '../game/economy.ts';
import { isGamePaused } from '../game/pause.ts';
import { findPlayer } from '../game/player-query.ts';
import { NpcIdleAnimator } from '../game/npc-anims.ts';
import { showToast } from '../../../shared/src/ui';

const MODEL_URL = '/assets/meshes/characters/npc_healer_lod2.glb';
const IDLE_CLIP = 'idle';
// Variedade de idle: conversa/braços cruzados; acena 'yes' ao curar.
const idleVariety = new NpcIdleAnimator({
  idle: IDLE_CLIP,
  gestures: ['talk', 'foldarms'],
});
const TURN_SPEED = 6;
const TALK_RANGE_SQ = 4.5 * 4.5;
const FACE_RANGE_SQ = 5 * 5;
const HEAL_PRICE = 20;

let group: THREE.Group | null = null;
let animator: GltfAnimator | null = null;
let yaw = 0;
let loadStarted = false;
let healerEid = 0;
let promptShown = false;
let fPressed = false;

function showPrompt(state: MonoBehaviourContext['state']): void {
  if (promptShown || !healerEid) return;
  registerInteractionTarget(state, healerEid, {
    label: `Curar (${HEAL_PRICE}g)`,
    key: 'F',
  });
  promptShown = true;
}

function hidePrompt(state: MonoBehaviourContext['state']): void {
  if (!promptShown || !healerEid) return;
  unregisterInteractionTarget(state, healerEid);
  promptShown = false;
}

function tryHeal(player: number): void {
  const max = Health.max[player] ?? 0;
  const cur = Health.current[player] ?? 0;
  if (max <= 0) return;
  if (cur >= max) {
    showToast('Já estás são.', {
      color: '#b8e0c8',
      borderColor: '#5a8a6a',
      background: 'rgba(12,22,16,0.95)',
    });
    playSound('error');
    return;
  }
  if (!spendGold(HEAL_PRICE)) {
    showToast(`A cura custa ${HEAL_PRICE} de ouro.`, {
      color: '#ffb0a0',
      borderColor: '#c06050',
      background: 'rgba(22,10,10,0.95)',
    });
    playSound('error');
    return;
  }
  healHealth(player, max - cur);
  playSound('heal');
  idleVariety.react(animator, 'yes');
  showToast('As feridas fecham.', {
    color: '#9fe8c0',
    borderColor: '#4caf7a',
    background: 'rgba(10,22,16,0.95)',
  });
}

export function start(ctx: MonoBehaviourContext): void {
  findPlayer(ctx.state);
  healerEid = ctx.entity;
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
    tryHeal(player);
  }
  fPressed = f;
}
