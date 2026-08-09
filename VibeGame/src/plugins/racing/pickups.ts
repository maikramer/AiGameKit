import * as THREE from 'three';
import { defineSystem, defineQuery, type State, type System } from '../../core';
import { getScene } from '../rendering';
import { getSoundDef, playSound } from '../audio';
import { PickupOrb, Track, Vehicle } from './components';
import { getTrackPickups, getTrackSpline } from './data';
import { WorldTransform } from '../transforms';
import { grantPowerUpAmmo } from './powerups';

/**
 * Pickup orbs left on the track. Each lives in track space; the system
 * resolves their world position every draw frame and tests player proximity
 * in world metres (the arc delta is more permissive but a proximity test
 * in metres matches what the player *sees*).
 */
const PICKUP_RANGE = 4.5;
/** Vertical offset (m) above the road surface so the orb floats over the asphalt. */
const PICKUP_HOVER = 1.2;

const _pos = { x: 0, y: 0, z: 0 };

/** Last pickup collected per vehicle, for the HUD's "+1" banner. */
const lastPickupByVehicle = new Map<number, { kind: number; time: number }>();

/** Most recent pickup a vehicle collected (HUD feedback). */
export function getLastPickup(
  eid: number
): { kind: number; time: number } | undefined {
  return lastPickupByVehicle.get(eid);
}

interface PickupVisual {
  group: THREE.Group;
  inner: THREE.Mesh;
  glow: THREE.Mesh;
  ring: THREE.Mesh;
  orbSlot: number;
}

const visuals: PickupVisual[] = [];

function pickColor(kind: number): number {
  return (
    [
      0x38e8ff, // Pulse — cyan
      0xff5dff, // Sidewinder — magenta
      0xffe066, // Shield — amber
    ][kind] ?? 0xffffff
  );
}

function buildOrbMesh(kind: number): PickupVisual {
  const group = new THREE.Group();
  group.name = 'PickupOrb';
  const color = pickColor(kind);

  const innerGeo = new THREE.IcosahedronGeometry(0.5, 1);
  const innerMat = new THREE.MeshStandardMaterial({
    color,
    emissive: color,
    emissiveIntensity: 1.6,
    transparent: true,
    opacity: 0.95,
    metalness: 0.2,
    roughness: 0.4,
  });
  const inner = new THREE.Mesh(innerGeo, innerMat);
  group.add(inner);

  const glowGeo = new THREE.SphereGeometry(0.95, 14, 12);
  const glowMat = new THREE.MeshBasicMaterial({
    color,
    transparent: true,
    opacity: 0.22,
    depthWrite: false,
  });
  const glow = new THREE.Mesh(glowGeo, glowMat);
  group.add(glow);

  const ringGeo = new THREE.TorusGeometry(0.95, 0.05, 8, 36);
  const ringMat = new THREE.MeshBasicMaterial({
    color: 0xffffff,
    transparent: true,
    opacity: 0.55,
    depthWrite: false,
  });
  const ring = new THREE.Mesh(ringGeo, ringMat);
  ring.rotation.x = Math.PI / 2;
  group.add(ring);

  return { group, inner, glow, ring, orbSlot: -1 };
}

function playBanked(key: string): void {
  if (getSoundDef(key)) playSound(key);
}

const vehicleQuery = defineQuery([Vehicle]);
const pickupQuery = defineQuery([PickupOrb]);
const trackQuery = defineQuery([Track]);

function resolveTrackEntity(state: State): number | undefined {
  return trackQuery(state.world)[0];
}

/**
 * PickupSystem — proximity test and respawn.
 *
 * Each orb lives in track-space. The system resolves world position every
 * frame, checks distance to *every* vehicle (player and rivals alike — the AI
 * collects orbs too), and toggles `ttl` between `>0` (live) and `<0` (cooling
 * down). Auto-respawn timer is `respawnAfter` seconds.
 */
export const PickupSystem: System = defineSystem({
  name: 'PickupSystem',
  group: 'fixed',

  update(state: State) {
    syncOrbEntities(state);
    const orbs = pickupQuery(state.world);
    if (orbs.length === 0) return;
    const trackEid = resolveTrackEntity(state);
    if (trackEid === undefined) return;
    const spline = getTrackSpline(trackEid);
    if (!spline) return;
    const vehicles = vehicleQuery(state.world);
    if (vehicles.length === 0) return;
    const dt = state.time.fixedDeltaTime;

    for (const eid of orbs) {
      const ttl = PickupOrb.ttl[eid] ?? 0;
      if (ttl > 0) {
        // The orb's world position is derived from its track-space anchor —
        // the orb entity itself has no Transform, so read the spline directly.
        spline.positionAt(
          PickupOrb.s[eid] ?? 0,
          PickupOrb.lateral[eid] ?? 0,
          PICKUP_HOVER,
          _pos
        );
        for (const veh of vehicles) {
          const dx = _pos.x - WorldTransform.posX[veh];
          const dy = _pos.y - WorldTransform.posY[veh];
          const dz = _pos.z - WorldTransform.posZ[veh];
          const dist = Math.hypot(dx, dy, dz);
          if (dist < PICKUP_RANGE) {
            const kind = PickupOrb.kind[eid] ?? 0;
            grantPowerUpAmmo(veh, kind, 1);
            PickupOrb.ttl[eid] = -Math.max(
              0.1,
              PickupOrb.respawnAfter[eid] || 0
            );
            lastPickupByVehicle.set(veh, {
              kind,
              time: performance.now(),
            });
            playBanked('race-pickup');
            break;
          }
        }
      } else if (ttl < 0) {
        PickupOrb.ttl[eid] = Math.min(0, ttl + dt);
        if (PickupOrb.ttl[eid]! >= 0) {
          PickupOrb.ttl[eid] = 1;
        }
      }
    }
  },
});

/**
 * PickupVisualSystem — keeps visuals in sync with the picker Orb entities.
 * The first frame after a new orb entity appears, builds a mesh; on collect
 * it hides the orb by scaling to zero.
 */
export const PickupVisualSystem: System = defineSystem({
  name: 'PickupVisualSystem',
  group: 'draw',

  update(state: State) {
    if (state.headless) return;
    const scene = getScene(state) as THREE.Scene | null;
    if (!scene) return;
    const trackEid = resolveTrackEntity(state);
    if (trackEid === undefined) return;
    const spline = getTrackSpline(trackEid);
    if (!spline) return;
    const t = state.time.elapsed;
    const pickups = getTrackPickups();

    // Ensure visuals match the entity list.
    syncVisuals(state, scene);

    for (let i = 0; i < pickups.length; i++) {
      const def = pickups[i]!;
      const v = visuals[i];
      if (!v) continue;
      const ttl = PickupOrb.ttl[def.eid] ?? 0;
      const visible = ttl > 0;
      v.group.visible = visible;
      if (!visible) continue;
      const pulse = 1 + Math.sin(t * 4 + i) * 0.08;
      v.group.scale.setScalar(pulse);
      v.inner.rotation.y += 0.04;
      v.inner.rotation.x += 0.02;
      v.ring.rotation.z = t * 0.6 + i;
      const hover = Math.sin(t * 2.4 + i * 0.7) * 0.18;
      spline.positionAt(def.s, def.lateral, PICKUP_HOVER + hover, _pos);
      v.group.position.set(_pos.x, _pos.y, _pos.z);
    }
  },
});

function syncVisuals(state: State, scene: THREE.Scene): void {
  const pickups = getTrackPickups();
  // Pickup definitions whose entity has not yet been created (also synced by
  // the fixed-step PickupSystem, so this is normally a no-op).
  syncOrbEntities(state);
  // Visuals whose pickup has been removed.
  if (visuals.length > pickups.length) {
    for (let i = pickups.length; i < visuals.length; i++) {
      const v = visuals[i]!;
      v.group.parent?.remove(v.group);
      v.inner.geometry.dispose();
      (v.inner.material as THREE.Material).dispose();
      v.glow.geometry.dispose();
      (v.glow.material as THREE.Material).dispose();
      v.ring.geometry.dispose();
      (v.ring.material as THREE.Material).dispose();
    }
    visuals.length = pickups.length;
  }
  // Visuals missing for an existing pickup.
  for (let i = visuals.length; i < pickups.length; i++) {
    const def = pickups[i]!;
    const v = buildOrbMesh(def.kind);
    v.orbSlot = def.eid;
    scene.add(v.group);
    visuals.push(v);
  }
}

/** Create PickupOrb entities for any registered pickups that lack one. */
function syncOrbEntities(state: State): void {
  const pickups = getTrackPickups();
  for (const def of pickups) {
    if (def.eid >= 0) continue;
    const eid = state.createEntity();
    state.addComponent(eid, PickupOrb);
    PickupOrb.s[eid] = def.s;
    PickupOrb.lateral[eid] = def.lateral;
    PickupOrb.kind[eid] = def.kind;
    PickupOrb.ttl[eid] = 1;
    PickupOrb.respawnAfter[eid] = def.respawnAfter;
    def.eid = eid;
  }
}

/** Dispose visuals (called from the plugin dispose). */
export function disposePickupVisuals(): void {
  for (const v of visuals) {
    v.group.parent?.remove(v.group);
    v.inner.geometry.dispose();
    (v.inner.material as THREE.Material).dispose();
    v.glow.geometry.dispose();
    (v.glow.material as THREE.Material).dispose();
    v.ring.geometry.dispose();
    (v.ring.material as THREE.Material).dispose();
  }
  visuals.length = 0;
}
