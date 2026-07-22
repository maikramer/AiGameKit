import * as THREE from 'three';
import { defineSystem, defineQuery } from '../../core';
import type { State, System } from '../../core';
import { getRenderingContext } from '../rendering';
import { Transform } from '../transforms/components';
import { CharacterMovement } from '../physics';
import { CharacterMovementSystem } from '../physics/systems';
import { spawnParticleBurst } from '../particles';
import { waterLevelAt } from './registry';

/** Max horizontal stride resistance while fully submerged (0..0.9). */
const MAX_WATER_DRAG = 0.55;
/** Submersion depth (m) at which drag reaches its maximum. */
const FULL_DRAG_DEPTH = 1.1;
/** Terminal sink speed (m/s) — water resists faster falls (buoyancy feel). */
const SINK_SPEED = 2.5;
/** Downward speed (m/s) at entry that triggers the big splash. */
const SPLASH_FALL_SPEED = 1.5;
/** Horizontal speed (m/s) above which wading spawns wake ripples. */
const WAKE_SPEED = 0.6;
/** Seconds between wake ripples per entity. */
const WAKE_INTERVAL = 0.3;
/** Horizontal speed (m/s) above which wading also kicks up droplets. */
const WAKE_DROPLET_SPEED = 2.5;

/**
 * Depth-proportional wading drag: 0 at the surface, {@link MAX_WATER_DRAG}
 * once submerged {@link FULL_DRAG_DEPTH} metres. Pure — unit tested.
 */
export function computeWaterDrag(submersion: number): number {
  const d = Math.min(Math.max(submersion / FULL_DRAG_DEPTH, 0), 1);
  return d * MAX_WATER_DRAG;
}

interface WadeState {
  wasIn: boolean;
  prevY: number;
  lastRippleAt: number;
  dropletToggle: boolean;
}

interface RippleFx {
  mesh: THREE.Mesh;
  material: THREE.MeshBasicMaterial;
  bornAt: number;
  life: number;
  startRadius: number;
  endRadius: number;
}

interface WaterFxContext {
  wade: Map<number, WadeState>;
  ripples: RippleFx[];
  ripplePool: RippleFx[];
  ringGeometry: THREE.RingGeometry | null;
}

const FX = new WeakMap<State, WaterFxContext>();

function fxContext(state: State): WaterFxContext {
  let ctx = FX.get(state);
  if (!ctx) {
    ctx = {
      wade: new Map(),
      ripples: [],
      ripplePool: [],
      ringGeometry: null,
    };
    FX.set(state, ctx);
  }
  return ctx;
}

const MAX_RIPPLES = 40;
const RIPPLE_LIFE = 0.9;

/**
 * Spawn an expanding cartoon ripple ring on the water surface. Shared unit
 * RingGeometry scaled per ring; each ring owns its (fading) material.
 */
export function spawnWaterRipple(
  state: State,
  x: number,
  y: number,
  z: number,
  startRadius: number,
  endRadius: number
): void {
  if (state.headless) return;
  const ctx = fxContext(state);
  if (ctx.ripples.length >= MAX_RIPPLES) return;
  if (!ctx.ringGeometry) {
    ctx.ringGeometry = new THREE.RingGeometry(0.82, 1, 40);
  }
  let ripple = ctx.ripplePool.pop();
  if (!ripple) {
    const material = new THREE.MeshBasicMaterial({
      color: 0xeafaff,
      transparent: true,
      opacity: 0.85,
      depthWrite: false,
    });
    ripple = {
      mesh: new THREE.Mesh(ctx.ringGeometry, material),
      material,
      bornAt: 0,
      life: RIPPLE_LIFE,
      startRadius,
      endRadius,
    };
  }
  const { mesh, material } = ripple;
  material.opacity = 0.85;
  mesh.rotation.x = -Math.PI / 2;
  mesh.position.set(x, y, z);
  mesh.scale.setScalar(startRadius);
  // Above the water disc (renderOrder 2) so rings never z-fight the surface.
  mesh.renderOrder = 3;
  getRenderingContext(state).scene.add(mesh);
  ripple.bornAt = state.time.elapsed;
  ripple.life = RIPPLE_LIFE;
  ripple.startRadius = startRadius;
  ripple.endRadius = endRadius;
  ctx.ripples.push(ripple);
}

const waderQuery = defineQuery([Transform, CharacterMovement]);

/**
 * Water gameplay feel for character movers (player and AI alike):
 * - entry splash (droplet burst + big ripple) when falling into a lake,
 * - wake ripples (and droplet kicks at speed) while wading,
 * - depth-proportional stride drag via {@link CharacterMovement.waterDrag},
 * - buoyant fall damping toward a terminal sink speed.
 *
 * Runs in the fixed group before the character controller consumes
 * desired velocities and waterDrag.
 */
export const WaterInteractionSystem: System = defineSystem({
  name: 'WaterInteractionSystem',
  group: 'fixed',
  before: [CharacterMovementSystem],
  update(state: State) {
    const ctx = fxContext(state);
    const dt = state.time.fixedDeltaTime;
    const now = state.time.elapsed;

    for (const eid of waderQuery(state.world)) {
      const x = Transform.posX[eid];
      const y = Transform.posY[eid];
      const z = Transform.posZ[eid];
      const waterY = waterLevelAt(state, x, z);

      let st = ctx.wade.get(eid);
      if (!st) {
        st = { wasIn: false, prevY: y, lastRippleAt: 0, dropletToggle: false };
        ctx.wade.set(eid, st);
        state.onDestroy(eid, () => {
          ctx.wade.delete(eid);
          CharacterMovement.waterDrag[eid] = 0;
        });
      }

      // Transform origin is the feet (project GLB convention), so submersion
      // is simply how far the surface sits above the entity origin.
      const submersion = waterY === null ? 0 : waterY - y;
      const inWater = waterY !== null && submersion > 0.03;

      CharacterMovement.waterDrag[eid] = inWater
        ? computeWaterDrag(submersion)
        : 0;

      if (inWater && waterY !== null) {
        // Water resists fast falls: ease vertical velocity toward the
        // terminal sink speed instead of hard-clamping (no pop on entry).
        const velY = CharacterMovement.velocityY[eid];
        if (velY < -SINK_SPEED) {
          CharacterMovement.velocityY[eid] +=
            (-SINK_SPEED - velY) * Math.min(1, dt * 6);
        }

        const fallSpeed = dt > 0 ? (st.prevY - y) / dt : 0;
        if (!st.wasIn && !state.headless) {
          if (fallSpeed > SPLASH_FALL_SPEED) {
            spawnParticleBurst(state, {
              x,
              y: waterY + 0.05,
              z,
              preset: 'splash',
              duration: 0.7,
            });
            spawnWaterRipple(state, x, waterY + 0.02, z, 0.5, 2.6);
          } else {
            spawnWaterRipple(state, x, waterY + 0.02, z, 0.35, 1.5);
          }
          st.lastRippleAt = now;
        }

        // Wake while wading: periodic rings, droplet kicks when sprinting.
        if (!state.headless && now - st.lastRippleAt >= WAKE_INTERVAL) {
          const speed =
            dt > 0
              ? Math.hypot(
                  CharacterMovement.actualMoveX[eid],
                  CharacterMovement.actualMoveZ[eid]
                ) / dt
              : 0;
          if (speed > WAKE_SPEED) {
            st.lastRippleAt = now;
            spawnWaterRipple(state, x, waterY + 0.02, z, 0.3, 1.4);
            st.dropletToggle = !st.dropletToggle;
            if (speed > WAKE_DROPLET_SPEED && st.dropletToggle) {
              spawnParticleBurst(state, {
                x,
                y: waterY + 0.05,
                z,
                preset: 'splash',
                duration: 0.5,
              });
            }
          }
        }
      }

      st.wasIn = inWater;
      st.prevY = y;
    }
  },
});

/** Expands and fades the live ripple rings; reclaims dead ones. */
export const WaterRippleFxSystem: System = defineSystem({
  name: 'WaterRippleFxSystem',
  group: 'draw',
  update(state: State) {
    if (state.headless) return;
    const ctx = FX.get(state);
    if (!ctx || ctx.ripples.length === 0) return;
    const now = state.time.elapsed;

    for (let i = ctx.ripples.length - 1; i >= 0; i--) {
      const r = ctx.ripples[i];
      const t = (now - r.bornAt) / r.life;
      if (t >= 1) {
        r.mesh.removeFromParent();
        ctx.ripples.splice(i, 1);
        ctx.ripplePool.push(r);
        continue;
      }
      // Ease-out expansion reads as a real wavefront losing energy.
      const ease = 1 - (1 - t) * (1 - t);
      r.mesh.scale.setScalar(
        r.startRadius + (r.endRadius - r.startRadius) * ease
      );
      r.material.opacity = 0.85 * (1 - t);
    }
  },
  dispose(state: State) {
    const ctx = FX.get(state);
    if (!ctx) return;
    for (const r of ctx.ripples) {
      r.mesh.removeFromParent();
      r.material.dispose();
    }
    for (const r of ctx.ripplePool) {
      r.material.dispose();
    }
    ctx.ripples.length = 0;
    ctx.ripplePool.length = 0;
    ctx.ringGeometry?.dispose();
    ctx.ringGeometry = null;
    FX.delete(state);
  },
});
