import * as THREE from 'three';
import { defineSystem, defineQuery, type State, type System } from '../../core';
import { getScene } from '../rendering';
import { WorldTransform } from '../transforms';
import { Vehicle } from './components';

const vehicleQuery = defineQuery([Vehicle]);

// Per-vehicle particle state.
const particleState = new Map<number, VehicleParticles>();

interface VehicleParticles {
  tireSmoke: THREE.Points | null;
  tireSparks: THREE.Points | null;
  smokePositions: Float32Array;
  smokeVelocities: Float32Array;
  smokeLifetimes: Float32Array;
  sparkPositions: Float32Array;
  sparkVelocities: Float32Array;
  sparkLifetimes: Float32Array;
  maxSmoke: number;
  maxSparks: number;
}

const SMOKE_COUNT = 120;
const SPARK_COUNT = 60;

/**
 * VehicleParticlesSystem — emits tire smoke during drift/handbrake and sparks
 * when scraping walls at speed. Uses raw THREE.Points for performance (no
 * quarks dependency) so it works in any VibeGame scene.
 *
 * Runs in `draw` group; spawns particles at wheel positions in world space.
 */
export const VehicleParticlesSystem: System = defineSystem({
  name: 'VehicleParticlesSystem',
  group: 'draw',

  update(state: State) {
    if (state.headless) return;
    const scene = getScene(state);
    if (!scene) return;

    const vehicles = vehicleQuery(state.world);

    for (const eid of vehicles) {
      let vp = particleState.get(eid);
      if (!vp) {
        vp = createParticleSystems(scene);
        particleState.set(eid, vp);
      }

      const speed = Vehicle.speed[eid] || 0;
      const steer = Math.abs(Vehicle.steer[eid] || 0);
      const handbrake = Vehicle.handbrake[eid] || 0;
      const roll = Math.abs(Vehicle.roll[eid] || 0);
      const speedFrac = Math.min(1, Math.abs(speed) / (Vehicle.maxSpeed[eid] || 40));

      // Get vehicle world position/orientation.
      const px = WorldTransform.posX[eid];
      const py = WorldTransform.posY[eid];
      const pz = WorldTransform.posZ[eid];

      // --- Tire Smoke (drift / hard cornering) -----------------------------
      // Emit when steering hard at speed or handbraking.
      const driftIntensity = handbrake > 0
        ? 0.8 + 0.2 * speedFrac
        : steer * speedFrac * (roll > 0.02 ? 2.5 : 0.4);

      if (driftIntensity > 0.15 && vp.tireSmoke) {
        emitSmoke(vp, px, py, pz, driftIntensity, state.time.deltaTime);
      }

      // Update existing smoke particles.
      updateSmoke(vp, state.time.deltaTime);

      // Write smoke positions to geometry.
      if (vp.tireSmoke) {
        const geo = vp.tireSmoke.geometry;
        geo.setAttribute('position', new THREE.Float32BufferAttribute(vp.smokePositions, 3));
        // Fade alpha based on lifetime.
        const colors = new Float32Array(vp.maxSmoke * 3);
        for (let i = 0; i < vp.maxSmoke; i++) {
          const t = vp.smokeLifetimes[i];
          const a = t > 0 ? Math.max(0, Math.min(1, t)) : 0;
          // Gray-white smoke.
          colors[i * 3] = a * 0.85;
          colors[i * 3 + 1] = a * 0.85;
          colors[i * 3 + 2] = a * 0.9;
        }
        geo.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
      }

      // --- Sparks (wall scrape simulation) ----------------------------------
      // Emit when at high speed with significant lateral slip (simplified).
      const sparkIntensity = speedFrac > 0.6 && roll > 0.06 ? roll * speedFrac * 1.5 : 0;

      if (sparkIntensity > 0.1 && vp.tireSparks) {
        emitSparks(vp, px, py, pz, sparkIntensity);
      }

      updateSparks(vp, state.time.deltaTime);

      if (vp.tireSparks) {
        const geo = vp.tireSparks.geometry;
        geo.setAttribute('position', new THREE.Float32BufferAttribute(vp.sparkPositions, 3));
        const colors = new Float32Array(vp.maxSparks * 3);
        for (let i = 0; i < vp.maxSparks; i++) {
          const t = vp.sparkLifetimes[i];
          const a = t > 0 ? Math.max(0, Math.min(1, t)) : 0;
          // Orange-yellow sparks.
          colors[i * 3] = a * 1.0;
          colors[i * 3 + 1] = a * 0.6;
          colors[i * 3 + 2] = a * 0.1;
        }
        geo.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
      }

      // Follow the vehicle (parent particles to scene, update positions manually).
      // Particles are in world space — they stay where emitted.
    }
  },

  dispose() {
    for (const vp of particleState.values()) {
      if (vp.tireSmoke) {
        vp.tireSmoke.geometry.dispose();
        (vp.tireSmoke.material as THREE.Material).dispose();
        vp.tireSmoke.parent?.remove(vp.tireSmoke);
      }
      if (vp.tireSparks) {
        vp.tireSparks.geometry.dispose();
        (vp.tireSparks.material as THREE.Material).dispose();
        vp.tireSparks.parent?.remove(vp.tireSparks);
      }
    }
    particleState.clear();
  },
});

function createParticleSystems(scene: THREE.Object3D): VehicleParticles {
  const maxSmoke = SMOKE_COUNT;
  const maxSparks = SPARK_COUNT;

  // Tire smoke: soft round points.
  const smokeGeo = new THREE.BufferGeometry();
  const smokePos = new Float32Array(maxSmoke * 3);
  smokeGeo.setAttribute('position', new THREE.Float32BufferAttribute(smokePos, 3));
  smokeGeo.setAttribute('color', new THREE.Float32BufferAttribute(new Float32Array(maxSmoke * 3), 3));
  const smokeMat = new THREE.PointsMaterial({
    size: 0.6,
    vertexColors: true,
    transparent: true,
    opacity: 0.35,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
    sizeAttenuation: true,
  });
  const tireSmoke = new THREE.Points(smokeGeo, smokeMat);
  tireSmoke.frustumCulled = false;
  scene.add(tireSmoke);

  // Sparks: small bright points.
  const sparkGeo = new THREE.BufferGeometry();
  const sparkPos = new Float32Array(maxSparks * 3);
  sparkGeo.setAttribute('position', new THREE.Float32BufferAttribute(sparkPos, 3));
  sparkGeo.setAttribute('color', new THREE.Float32BufferAttribute(new Float32Array(maxSparks * 3), 3));
  const sparkMat = new THREE.PointsMaterial({
    size: 0.12,
    vertexColors: true,
    transparent: true,
    opacity: 1.0,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
    sizeAttenuation: true,
  });
  const tireSparks = new THREE.Points(sparkGeo, sparkMat);
  tireSparks.frustumCulled = false;
  scene.add(tireSparks);

  return {
    tireSmoke,
    tireSparks,
    smokePositions: smokePos,
    smokeVelocities: new Float32Array(maxSmoke * 3),
    smokeLifetimes: new Float32Array(maxSmoke),
    sparkPositions: sparkPos,
    sparkVelocities: new Float32Array(maxSparks * 3),
    sparkLifetimes: new Float32Array(maxSparks),
    maxSmoke,
    maxSparks,
  };
}

function emitSmoke(
  vp: VehicleParticles,
  vx: number,
  vy: number,
  vz: number,
  intensity: number,
  dt: number
): void {
  // Emit ~N particles per second based on intensity.
  const rate = intensity * 40; // particles/sec
  const count = Math.floor(rate * dt) + (Math.random() < (rate * dt % 1) ? 1 : 0);

  // Wheel offsets (rear wheels for drift smoke).
  const wheelOffsets = [
    [-0.54, 0.2, -0.68],
    [0.54, 0.2, -0.68],
  ];

  for (let c = 0; c < count; c++) {
    // Find a dead slot.
    let slot = -1;
    for (let i = 0; i < vp.maxSmoke; i++) {
      if (vp.smokeLifetimes[i] <= 0) {
        slot = i;
        break;
      }
    }
    if (slot < 0) slot = Math.floor(Math.random() * vp.maxSmoke); // overwrite oldest

    const wo = wheelOffsets[c % wheelOffsets.length]!;
    const i3 = slot * 3;
    vp.smokePositions[i3] = vx + wo[0] + (Math.random() - 0.5) * 0.15;
    vp.smokePositions[i3 + 1] = vy + wo[1];
    vp.smokePositions[i3 + 2] = vz + wo[2] + (Math.random() - 0.5) * 0.15;

    // Velocity: mostly up + backward with spread.
    vp.smokeVelocities[i3] = (Math.random() - 0.5) * 1.5;
    vp.smokeVelocities[i3 + 1] = 1.0 + Math.random() * 1.5; // rise
    vp.smokeVelocities[i3 + 2] = (Math.random() - 0.5) * 1.5 - 2.0; // backward

    vp.smokeLifetimes[slot] = 0.8 + Math.random() * 0.7; // 0.8–1.5s life
  }
}

function updateSmoke(vp: VehicleParticles, dt: number): void {
  for (let i = 0; i < vp.maxSmoke; i++) {
    if (vp.smokeLifetimes[i] <= 0) continue;
    const i3 = i * 3;
    // Apply velocity.
    vp.smokePositions[i3] += vp.smokeVelocities[i3] * dt;
    vp.smokePositions[i3 + 1] += vp.smokeVelocities[i3 + 1] * dt;
    vp.smokePositions[i3 + 2] += vp.smokeVelocities[i3 + 2] * dt;
    // Slow rise, expand sideways.
    vp.smokeVelocities[i3 + 1] *= 0.98; // drag
    vp.smokeVelocities[i3] *= 0.96;
    vp.smokeVelocities[i3 + 2] *= 0.96;
    // Age.
    vp.smokeLifetimes[i] -= dt;
  }
}

function emitSparks(
  vp: VehicleParticles,
  vx: number,
  vy: number,
  vz: number,
  intensity: number
): void {
  const count = Math.floor(intensity * 8) + 1;

  for (let c = 0; c < count; c++) {
    let slot = -1;
    for (let i = 0; i < vp.maxSparks; i++) {
      if (vp.sparkLifetimes[i] <= 0) {
        slot = i;
        break;
      }
    }
    if (slot < 0) slot = Math.floor(Math.random() * vp.maxSparks);

    const i3 = slot * 3;
    // Emit near the body base.
    vp.sparkPositions[i3] = vx + (Math.random() - 0.5) * 1.0;
    vp.sparkPositions[i3 + 1] = vy + 0.1 + Math.random() * 0.2;
    vp.sparkPositions[i3 + 2] = vz + (Math.random() - 0.5) * 2.0;

    // Fast outward velocity.
    const angle = Math.random() * Math.PI * 2;
    const speed = 3 + Math.random() * 6;
    vp.sparkVelocities[i3] = Math.cos(angle) * speed;
    vp.sparkVelocities[i3 + 1] = 1 + Math.random() * 3;
    vp.sparkVelocities[i3 + 2] = Math.sin(angle) * speed;

    vp.sparkLifetimes[slot] = 0.15 + Math.random() * 0.25; // short bright life
  }
}

function updateSparks(vp: VehicleParticles, dt: number): void {
  for (let i = 0; i < vp.maxSparks; i++) {
    if (vp.sparkLifetimes[i] <= 0) continue;
    const i3 = i * 3;
    vp.sparkPositions[i3] += vp.sparkVelocities[i3] * dt;
    vp.sparkPositions[i3 + 1] += vp.sparkVelocities[i3 + 1] * dt;
    vp.sparkPositions[i3 + 2] += vp.sparkVelocities[i3 + 2] * dt;
    // Gravity.
    vp.sparkVelocities[i3 + 1] -= 9.8 * dt;
    vp.sparkLifetimes[i] -= dt;
  }
}
