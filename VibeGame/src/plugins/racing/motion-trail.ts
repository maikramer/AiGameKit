import * as THREE from 'three';
import { defineSystem, defineQuery, type State, type System } from '../../core';
import { getScene } from '../rendering';
import { WorldTransform } from '../transforms';
import { Vehicle } from './components';

const vehicleQuery = defineQuery([Vehicle]);

interface TrailState {
  group: THREE.Group;
  trailPositions: Float32Array;
  trailAlphas: Float32Array;
  trailNormals: Float32Array; // for ribbon width
  maxPoints: number;
  nextIndex: number;
  count: number;
  mesh: THREE.Mesh | null;
  _accum: number; // sample-rate accumulator
}

const MAX_TRAIL_POINTS = 80;

/**
 * MotionTrailSystem — renders a fading afterimage / motion blur trail behind the
 * vehicle. Uses a ribbon mesh that follows the car's recent path with alpha-fading
 * vertices toward the tail.
 *
 * This is NOT a post-processing motion blur (that's in PostProcessingSystem).
 * This is a geometric trail attached to the car for that NFS "speed lines on the
 * road" feel combined with an actual visible wake.
 */
export const MotionTrailSystem: System = defineSystem({
  name: 'MotionTrailSystem',
  group: 'draw',

  update(state: State) {
    if (state.headless) return;
    const scene = getScene(state);
    if (!scene) return;

    const vehicles = vehicleQuery(state.world);

    for (const eid of vehicles) {
      let trail = (state as unknown as { __motionTrail?: Map<number, TrailState> }).__motionTrail?.get(eid);
      if (!trail) {
        trail = createTrail(scene);
        if (!(state as unknown as { __motionTrail?: Map<number, TrailState> }).__motionTrail) {
          (state as unknown as { __motionTrail?: Map<number, TrailState> }).__motionTrail = new Map();
        }
        (state as unknown as { __motionTrail?: Map<number, TrailState> }).__motionTrail!.set(eid, trail);
      }

      const speed = Vehicle.speed[eid] || 0;
      const maxSpeed = Vehicle.maxSpeed[eid] || 1;
      const speedFrac = Math.abs(speed) / maxSpeed;

      // Only record trail when moving significantly.
      if (speedFrac > 0.12) {
        const px = WorldTransform.posX[eid];
        const py = WorldTransform.posY[eid] + 0.15; // slightly above ground
        const pz = WorldTransform.posZ[eid];

        // Sample rate: faster = more points (smoother trail).
        const sampleRate = 0.04 + speedFrac * 0.06; // seconds between samples
        const dt = state.time.deltaTime;

        // Accumulate time and decide whether to add point.
        (trail as unknown as { _accum?: number })._accum =
          ((trail as unknown as { _accum?: number })._accum || 0) + dt;

        if ((trail as unknown as { _accum?: number })._accum! >= sampleRate) {
          (trail as unknown as { _accum?: number })._accum = 0;

          const i3 = trail.nextIndex * 3;
          trail.trailPositions[i3] = px;
          trail.trailPositions[i3 + 1] = py;
          trail.trailPositions[i3 + 2] = pz;

          // Alpha: newest = 1.0, fades toward tail.
          trail.trailAlphas[trail.nextIndex] = 1.0;

          trail.nextIndex = (trail.nextIndex + 1) % trail.maxPoints;
          trail.count = Math.min(trail.count + 1, trail.maxPoints);
        }
      } else {
        // Not moving fast enough — decay existing trail quickly.
        (trail as unknown as { _accum?: number })._accum = 0;
      }

      // Decay all alphas.
      const decayRate = 0.8 * state.time.deltaTime; // ~1.25s full fade when stopped
      for (let i = 0; i < trail.maxPoints; i++) {
        if (trail.trailAlphas[i] > 0) {
          trail.trailAlphas[i] -= decayRate;
          if (trail.trailAlphas[i] < 0) trail.trailAlphas[i] = 0;
        }
      }

      // Rebuild ribbon geometry.
      rebuildTrailMesh(trail);

      // Position trail group at vehicle (it's in world space but we want it to follow).
      if (trail.group.parent !== scene) {
        scene.add(trail.group);
      }
    }
  },

  dispose() {
    const map = ({} as unknown as { __motionTrail?: Map<number, TrailState> }).__motionTrail;
    if (map) {
      for (const trail of map.values()) {
        if (trail.mesh) {
          trail.mesh.geometry.dispose();
          (trail.mesh.material as THREE.Material).dispose();
        }
        trail.group.parent?.remove(trail.group);
      }
      map.clear();
    }
  },
});

function createTrail(scene: THREE.Object3D): TrailState {
  const group = new THREE.Group();
  group.name = 'MotionTrail';
  scene.add(group);

  const positions = new Float32Array(MAX_TRAIL_POINTS * 3);
  const alphas = new Float32Array(MAX_TRAIL_POINTS);
  const normals = new Float32Array(MAX_TRAIL_POINTS * 3); // unused but for structure

  return {
    group,
    trailPositions: positions,
    trailAlphas: alphas,
    trailNormals: normals,
    maxPoints: MAX_TRAIL_POINTS,
    nextIndex: 0,
    count: 0,
    mesh: null,
    _accum: 0,
  };
}

function rebuildTrailMesh(trail: TrailState): void {
  if (trail.count < 2) {
    if (trail.mesh) {
      trail.mesh.visible = false;
    }
    return;
  }

  // Build ribbon from trail points (newest → oldest order for proper fade).
  const positions: number[] = [];
  const colors: number[] = [];
  const indices: number[] = [];

  let idx = trail.nextIndex - 1;
  if (idx < 0) idx = trail.maxPoints - 1;

  // Start from oldest visible point.
  const startIdx = (trail.nextIndex - trail.count + trail.maxPoints) % trail.maxPoints;

  let prevValid = false;

  for (let step = 0; step < trail.count; step++) {
    const i = (startIdx + step) % trail.maxPoints;
    const alpha = trail.trailAlphas[i];
    if (alpha <= 0.01) continue; // skip fully faded

    const i3 = i * 3;
    const x = trail.trailPositions[i3];
    const y = trail.trailPositions[i3 + 1];
    const z = trail.trailPositions[i3 + 2];

    // Two vertices per point (left/right edge of ribbon).
    // Width tapers toward tail.
    const width = 0.15 + alpha * 0.35; // wider at head, narrow at tail

    // For simplicity, emit two vertices offset in X (will be oriented later).
    // In a full implementation we'd compute the perpendicular to movement direction.
    positions.push(x - width, y, z);
    positions.push(x + width, y, z);

    // Color: speed color (cyan/white) with alpha fade.
    const intensity = alpha;
    colors.push(intensity * 0.3, intensity * 0.7, intensity * 1.0, intensity * 0.6); // left
    colors.push(intensity * 0.3, intensity * 0.7, intensity * 1.0, intensity * 0.6); // right

    if (prevValid && step > 0) {
      const base = (step - 1) * 2;
      indices.push(base, base + 1, base + 2);
      indices.push(base + 1, base + 3, base + 2);
    }

    prevValid = true;
  }

  if (positions.length < 6) {
    if (trail.mesh) trail.mesh.visible = false;
    return;
  }

  // Create or update mesh.
  let mesh = trail.mesh;
  if (!mesh) {
    const geo = new THREE.BufferGeometry();
    const mat = new THREE.MeshBasicMaterial({
      vertexColors: true,
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
      side: THREE.DoubleSide,
    });
    mesh = new THREE.Mesh(geo, mat);
    mesh.frustumCulled = false;
    mesh.name = 'TrailRibbon';
    trail.group.add(mesh);
    trail.mesh = mesh;
  }

  const geo = mesh.geometry;
  geo.setAttribute('position', new THREE.Float32BufferAttribute(new Float32Array(positions), 3));
  geo.setAttribute('color', new THREE.Float32BufferAttribute(new Float32Array(colors), 4));
  if (indices.length > 0) {
    geo.setIndex(indices);
  }
  geo.computeVertexNormals();

  mesh.visible = true;
}
