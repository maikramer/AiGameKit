// Reusable "mystic object" behaviour: a static GLB prop (from public/assets) that
// glows with an emissive pulse to signal it is interactive, and — when the player
// walks up and presses F — shows a mystic line, grants a one-time reward, and goes
// dark (consumed). Each placement uses its own thin MonoBehaviour wrapper script so
// the per-object module state stays isolated (one instance per script, matching the
// merchant/chest convention).

import * as THREE from 'three';
import { defineQuery, loadGltfToSceneWithAnimator, playSound } from 'vibegame';
import type { MonoBehaviourContext, State } from 'vibegame';
import {
  Transform,
  PlayerController,
  getTerrainHeightAt,
  getBvhSurfaceHeight,
  isKeyDown,
} from 'vibegame';

const TERRAIN_LAYER = 0x0001;
const playerQuery = defineQuery([PlayerController]);

let toast: HTMLDivElement | null = null;
let toastTimeout: ReturnType<typeof setTimeout> | null = null;

/** Shared centre-screen mystic banner (one DOM node reused by every object). */
function showMysticToast(message: string, color: string): void {
  if (!toast) {
    toast = document.createElement('div');
    toast.style.cssText =
      'position:fixed;top:24%;left:50%;transform:translateX(-50%);max-width:60vw;' +
      'background:rgba(14,10,22,0.95);border:2px solid currentColor;border-radius:10px;' +
      'padding:16px 26px;z-index:1000;font:italic 19px Georgia,serif;text-align:center;' +
      'text-shadow:0 0 12px currentColor;box-shadow:0 0 30px rgba(140,90,255,0.35);' +
      'opacity:0;transition:opacity 0.25s;';
    document.body.appendChild(toast);
  }
  toast.style.color = color;
  toast.textContent = message;
  toast.style.opacity = '1';
  if (toastTimeout) clearTimeout(toastTimeout);
  toastTimeout = setTimeout(() => {
    if (toast) toast.style.opacity = '0';
  }, 3200);
}

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
  /** Extra lift above the terrain surface (metres). */
  yOffset?: number;
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
  const yOffset = cfg.yOffset ?? 0;

  let group: THREE.Group | null = null;
  let footOffset = 0;
  let loadStarted = false;
  let read = false;
  let fPressed = false;
  let cachedPlayer = 0;
  const emissiveMats: THREE.MeshStandardMaterial[] = [];
  const _box = new THREE.Box3();

  function findPlayer(ctx: MonoBehaviourContext): number {
    if (cachedPlayer && Transform.posX[cachedPlayer] !== undefined)
      return cachedPlayer;
    cachedPlayer = playerQuery(ctx.state.world)[0] ?? 0;
    return cachedPlayer;
  }

  function start(ctx: MonoBehaviourContext): void {
    findPlayer(ctx);
    if (loadStarted) return;
    loadStarted = true;
    void loadGltfToSceneWithAnimator(ctx.state, cfg.modelUrl).then((result) => {
      group = result.group;
      _box.setFromObject(group);
      footOffset = Number.isFinite(_box.min.y) ? -_box.min.y : 0;
      const col = new THREE.Color(cfg.emissiveColor);
      group.traverse((o) => {
        const mesh = o as THREE.Mesh;
        if (!mesh.isMesh) return;
        const mat = mesh.material as THREE.MeshStandardMaterial;
        if (mat && 'emissiveIntensity' in mat) {
          mat.emissive = col.clone();
          mat.emissiveIntensity = baseI;
          emissiveMats.push(mat);
        }
      });
    });
  }

  function update(ctx: MonoBehaviourContext): void {
    if (!group) return;
    const eid = ctx.entity;
    const x = Transform.posX[eid];
    const z = Transform.posZ[eid];
    const gy =
      getBvhSurfaceHeight(ctx.state, x, 500, z, 2000, TERRAIN_LAYER) ??
      getTerrainHeightAt(ctx.state, x, z);
    group.position.set(x, gy + footOffset + yOffset, z);

    if (read) return;

    // Pulse the glow while still unread.
    const pulse =
      baseI + pulseI * (0.5 + 0.5 * Math.sin(ctx.state.time.elapsed * 3));
    for (const m of emissiveMats) m.emissiveIntensity = pulse;

    const player = findPlayer(ctx);
    if (!player) return;
    const dx = Transform.posX[player] - x;
    const dz = Transform.posZ[player] - z;
    if (dx * dx + dz * dz >= readRangeSq) return;

    const f = isKeyDown('KeyF');
    if (f && !fPressed) {
      read = true;
      for (const m of emissiveMats) m.emissiveIntensity = 0;
      cfg.onRead(ctx.state, player);
      showMysticToast(cfg.message, toastColor);
      playSound('levelup');
    }
    fPressed = f;
  }

  return { start, update };
}
