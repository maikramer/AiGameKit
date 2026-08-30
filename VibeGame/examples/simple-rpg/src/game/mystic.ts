// Reusable "mystic object" behaviour: a static GLB prop (from public/assets) that
// glows with an emissive pulse to signal it is interactive, and — when the player
// walks up and presses F — shows a mystic line, grants a one-time reward, and goes
// dark (consumed). Each placement uses its own thin MonoBehaviour wrapper script so
// the per-object module state stays isolated (one instance per script, matching the
// merchant/chest convention).

import * as THREE from 'three';
import { loadGltfToSceneWithAnimator, playSound } from 'aigamekit-vibegame';
import type {
  InteractionGesture,
  MonoBehaviourContext,
  State,
} from 'aigamekit-vibegame';
import {
  Transform,
  isKeyDown,
  registerInteractionTarget,
  unregisterInteractionTarget,
} from 'aigamekit-vibegame';
import { showToast } from '../../../shared/src/ui';
import { findPlayer } from './player-query';

export interface MysticConfig {
  /** GLB to load (single static prop). */
  modelUrl: string;
  /** Squared read distance (metres²). Default 9 (=3 m). */
  readRangeSq?: number;
  /** Emissive glow colour (hex). */
  emissiveColor: number;
  /** Steady emissive floor. Default 0.4. */
  emissiveBase?: number;
  /** Added sinusoidal pulse on top of the floor. Default 0.45. */
  emissivePulse?: number;
  /** Banner colour. Defaults to a soft arcane violet. */
  toastColor?: string;
  /** The mystic line shown on read. */
  message: string;
  /** Button-prompt label shown when the player is in range (e.g. "Read"). */
  promptLabel: string;
  /**
   * Player body gesture on F. Use `'gather'` for ground collect/eat/pick-up;
   * omit or `'none'` for read/touch/enter-style interactions.
   */
  gesture?: InteractionGesture;
  /** Uniform visual scale (default 1) — pipeline GLBs are ~2 units tall. */
  modelScale?: number;
  /** One-time reward applied when the player reads the object. */
  onRead: (state: State, player: number) => void;
}

export interface MysticBehaviour {
  start: (ctx: MonoBehaviourContext) => void;
  update: (ctx: MonoBehaviourContext) => void;
}

export function createMysticObject(cfg: MysticConfig): MysticBehaviour {
  const readRangeSq = cfg.readRangeSq ?? 9;
  const baseI = cfg.emissiveBase ?? 0.4;
  const pulseI = cfg.emissivePulse ?? 0.45;
  const toastColor = cfg.toastColor ?? '#c9a6ff';
  const gesture: InteractionGesture = cfg.gesture ?? 'none';

  let group: THREE.Group | null = null;
  let loadStarted = false;
  /** Model failed to stream — stop offering interaction over nothing. */
  let loadFailed = false;
  let read = false;
  let fPressed = false;
  let entityId = 0;
  const emissiveMats: THREE.MeshStandardMaterial[] = [];

  function start(ctx: MonoBehaviourContext): void {
    findPlayer(ctx.state);
    entityId = ctx.entity;
    // Button prompt ("Press F …") shown by the engine InteractionPrompt widget
    // whenever the player is within its range of this entity.
    registerInteractionTarget(ctx.state, entityId, {
      label: cfg.promptLabel,
      key: 'F',
      gesture,
    });
    if (loadStarted) return;
    loadStarted = true;
    void loadGltfToSceneWithAnimator(ctx.state, cfg.modelUrl)
      .then((result) => {
        group = result.group;
        const scale = cfg.modelScale ?? 1;
        if (scale !== 1) group.scale.setScalar(scale);
        const col = new THREE.Color(cfg.emissiveColor);
        group.traverse((o) => {
          const mesh = o as THREE.Mesh;
          if (!mesh.isMesh) return;
          // Multi-material meshes expose an array; only swap single standard
          // materials (the glow is cosmetic, skipping is harmless).
          const mat = mesh.material as THREE.MeshStandardMaterial;
          if (mat && !Array.isArray(mat) && 'emissiveIntensity' in mat) {
            mat.emissive = col.clone();
            mat.emissiveIntensity = baseI;
            // Some source GLBs (the pillar) have inverted/one-sided normals that
            // make the interior show through; render both sides to avoid it.
            mat.side = THREE.DoubleSide;
            emissiveMats.push(mat);
          }
        });
      })
      .catch((err) => {
        // Missing/corrupt GLB: without this the interaction prompt floats over
        // nothing forever and the rejection goes unhandled. Retire the object
        // instead — one warn, no ghost prompt.
        console.warn('[mystic] failed to load', cfg.modelUrl, err);
        loadFailed = true;
        unregisterInteractionTarget(ctx.state, entityId);
      });
  }

  function update(ctx: MonoBehaviourContext): void {
    if (loadFailed) return;
    if (!group) return;
    const eid = ctx.entity;
    const x = Transform.posX[eid];
    const y = Transform.posY[eid];
    const z = Transform.posZ[eid];
    group.position.set(x, y, z);

    if (read) return;

    // Pulse the glow while still unread.
    const pulse =
      baseI + pulseI * (0.5 + 0.5 * Math.sin(ctx.state.time.elapsed * 3));
    for (const m of emissiveMats) m.emissiveIntensity = pulse;

    const player = findPlayer(ctx.state);
    if (!player) return;
    const dx = Transform.posX[player] - x;
    const dz = Transform.posZ[player] - z;
    if (dx * dx + dz * dz >= readRangeSq) return;

    const f = isKeyDown('KeyF');
    if (f && !fPressed) {
      read = true;
      for (const m of emissiveMats) m.emissiveIntensity = 0;
      unregisterInteractionTarget(ctx.state, entityId);
      cfg.onRead(ctx.state, player);
      // Shared mystic banner — one DOM node reused by every object in the game.
      showToast(cfg.message, {
        color: toastColor,
        top: '24%',
        maxWidth: '60vw',
        font: 'italic 19px Georgia,serif',
        background: 'rgba(14,10,22,0.95)',
        borderColor: toastColor,
        glow: '0 0 30px rgba(140,90,255,0.35)',
        textGlow: '0 0 12px currentColor',
        durationMs: 3200,
      });
      playSound('levelup');
    }
    fPressed = f;
  }

  return { start, update };
}
