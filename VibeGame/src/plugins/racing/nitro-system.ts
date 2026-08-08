import * as THREE from 'three';
import { defineSystem, defineQuery, type State, type System } from '../../core';
import { isKeyDown } from '../input';
import { getScene } from '../rendering';
import { getSoundDef, playSound } from '../audio';
import { WorldTransform } from '../transforms';
import { Rigidbody } from '../physics/components';
import { Vehicle, PlayerVehicle } from './components';
import { isRacingActive } from './race-state';

const playerVehicleQuery = defineQuery([Vehicle, PlayerVehicle, Rigidbody]);

// Per-player nitro state.
const nitroState = new Map<number, NitroData>();

interface NitroData {
  active: boolean;
  amount: number;        // 0..100 (percentage)
  maxAmount: number;
  rechargeRate: number;  // % per second when not active
  drainRate: number;     // % per second when active
  speedMultiplier: number;
  // Visual effects.
  flameGroup: THREE.Group | null;
  flameMeshes: THREE.Mesh[];
  screenFlash: number;   // 0..1 intensity
  lastFlameTime: number;
}

const DEFAULT_NITRO: Omit<NitroData, 'flameGroup' | 'flameMeshes'> = {
  active: false,
  amount: 100,
  maxAmount: 100,
  rechargeRate: 12,     // recharge in ~8s
  drainRate: 28,        // drains in ~3.5s
  speedMultiplier: 1.65, // +65% speed boost
  screenFlash: 0,
  lastFlameTime: 0,
};

/**
 * NitroSystem — adds a nitrous oxide boost mechanic (NFS-style).
 *
 * Controls:
 * - **LEFT SHIFT** to activate (hold)
 * - Auto-depletes while active, recharges when released
 *
 * Visual effects when active:
 * - Exhaust flames (animated mesh behind car)
 * - Screen flash/tint (blue-orange overlay)
 * - Speed lines intensify via SpeedEffectsSystem
 * - Camera shake via ChaseCamera FOV spike
 */
export const NitroSystem: System = defineSystem({
  name: 'NitroSystem',
  group: 'fixed', // runs with vehicle control so we can modify velocity

  update(state: State) {
    if (!isRacingActive()) return;

    const scene = getScene(state);
    if (!scene) return;

    const players = playerVehicleQuery(state.world);
    const player = players[0] ?? -1;
    if (player < 0) return;

    let nitro = nitroState.get(player);
    if (!nitro) {
      nitro = {
        ...DEFAULT_NITRO,
        flameGroup: null,
        flameMeshes: [],
      };
      nitroState.set(player, nitro);
    }

    // --- Input ---------------------------------------------------------------
    const wantsNitro = isKeyDown('ShiftLeft') || isKeyDown('ShiftRight');
    const canActivate = wantsNitro && nitro.amount > 5;

    // Toggle state.
    const wasActive = nitro.active;
    nitro.active = canActivate;
    if (!wasActive && nitro.active) {
      // Activation whoosh (bank key `race-nitro`; no-op when undefined).
      if (getSoundDef('race-nitro')) playSound('race-nitro');
    }

    // --- Drain / Recharge ---------------------------------------------------
    const dt = state.time.fixedDeltaTime;
    if (nitro.active && nitro.amount > 0) {
      nitro.amount -= nitro.drainRate * dt;
      if (nitro.amount < 0) nitro.amount = 0;
      // Screen flash on activation.
      if (!wasActive) {
        nitro.screenFlash = 1.0;
      }
    } else if (!nitro.active && nitro.amount < nitro.maxAmount) {
      nitro.amount += nitro.rechargeRate * dt;
      if (nitro.amount > nitro.maxAmount) nitro.amount = nitro.maxAmount;
    }

    // --- Apply speed boost --------------------------------------------------
    if (nitro.active && nitro.amount > 0) {
      // Multiply current speed toward a boosted cap.
      const currentSpeed = Vehicle.speed[player];
      const maxSpeed = Vehicle.maxSpeed[player] || 40;
      const boostedMax = maxSpeed * nitro.speedMultiplier;

      if (currentSpeed >= 0 && currentSpeed < boostedMax) {
        // Gentle push toward boosted max.
        const push = (boostedMax - currentSpeed) * 2.0 * dt;
        Vehicle.speed[player] = Math.min(boostedMax, currentSpeed + push);
      }
    }

    // Decay screen flash.
    if (nitro.screenFlash > 0) {
      nitro.screenFlash -= dt * 2.5;
      if (nitro.screenFlash < 0) nitro.screenFlash = 0;
    }

    // --- Exhaust Flames (visual) ---------------------------------------------
    if (nitro.active && nitro.amount > 0) {
      ensureFlames(nitro, scene, player);

      // Animate flames: scale oscillates, color shifts.
      const now = performance.now() * 0.001;
      const phase = (now - nitro.lastFlameTime) * 15; // fast flicker

      for (let i = 0; i < nitro.flameMeshes.length; i++) {
        const flame = nitro.flameMeshes[i];
        const flicker = 0.6 + 0.4 * Math.sin(phase + i * 0.7);
        const baseScale = 0.3 + nitro.screenFlash * 0.4;

        flame.scale.set(
          baseScale * flicker,
          baseScale * (1.5 + Math.random() * 1.5) * flicker,
          baseScale * flicker
        );

        // Color: blue core → orange tip (shifts with intensity).
        const mat = flame.material as THREE.MeshStandardMaterial & {
          emissive: THREE.Color;
          emissiveIntensity: number;
          color: THREE.Color;
        };
        const t = nitro.amount / nitro.maxAmount; // 1=full, 0=empty
        mat.color.lerpColors(
          new THREE.Color(0x0066ff), // blue (full nitro)
          new THREE.Color(0xff4400), // orange (depleting)
          1 - t
        );
        mat.emissive.copy(mat.color);
        mat.emissiveIntensity = 2.0 + flicker * 2.0;
      }

      nitro.lastFlameTime = now;

      // Position flames at exhaust pipes.
      const px = WorldTransform.posX[player];
      const py = WorldTransform.posY[player];
      const pz = WorldTransform.posZ[player];

      if (nitro.flameGroup) {
        nitro.flameGroup.position.set(px, py + 0.22, pz - 1.1);
        // Copy vehicle rotation.
        nitro.flameGroup.quaternion.set(
          WorldTransform.rotX[player],
          WorldTransform.rotY[player],
          WorldTransform.rotZ[player],
          WorldTransform.rotW[player]
        );
      }
    } else {
      // Hide flames.
      if (nitro.flameGroup) {
        nitro.flameGroup.visible = false;
      }
    }

    // Store for HUD/accessors.
    (state as unknown as { __nitro?: NitroData }).__nitro = nitro;
  },

  dispose() {
    for (const nitro of nitroState.values()) {
      if (nitro.flameGroup) {
        nitro.flameGroup.parent?.remove(nitro.flameGroup);
        for (const m of nitro.flameMeshes) {
          m.geometry.dispose();
          (m.material as THREE.Material).dispose();
        }
      }
    }
    nitroState.clear();
  },
});

function ensureFlames(nitro: NitroData, scene: THREE.Object3D, _eid: number): void {
  if (nitro.flameGroup) {
    nitro.flameGroup.visible = true;
    return;
  }

  const group = new THREE.Group();
  group.name = 'NitroFlames';

  // Two flame cones (one per exhaust pipe).
  const offsets = [-0.18, 0.18]; // X offsets matching exhaust positions.

  for (const ox of offsets) {
    // Cone geometry pointing backward (-Z).
    const geo = new THREE.ConeGeometry(0.08, 0.6, 8, 1, true);
    geo.rotateX(Math.PI / 2); // point along -Z
    const mat = new THREE.MeshStandardMaterial({
      color: 0x00aaff,
      emissive: 0x00aaff,
      emissiveIntensity: 2.5,
      transparent: true,
      opacity: 0.9,
      side: THREE.DoubleSide,
    });
    const flame = new THREE.Mesh(geo, mat);
    flame.position.set(ox, 0, 0);
    group.add(flame);
    nitro.flameMeshes.push(flame);
  }

  scene.add(group);
  nitro.flameGroup = group;
}
