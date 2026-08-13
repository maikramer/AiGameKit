import * as THREE from 'three';
import { defineSystem, defineQuery, type State, type System } from '../../core';
import { getScene } from '../rendering';
import { PlayerVehicle, RaceTracker, Track, Vehicle } from './components';
import { getTrackSpline } from './data';
import { getRaceState } from './race-state';
import { ghostWorldPose, sampleGhostAtTime } from './ghost';

const playerQuery = defineQuery([PlayerVehicle, Vehicle, RaceTracker]);
const trackQuery = defineQuery([Track]);

const GHOST_COLOR = 0x38e8ff;
const RIDE_HEIGHT = 0.38;

interface GhostVisual {
  group: THREE.Group;
  hull: THREE.Mesh;
}

let visual: GhostVisual | null = null;

/**
 * Translucent hologram of the personal-best lap. Not a Vehicle — it must not
 * collide, rubber-band, or show up in the standings.
 */
export const GhostVisualSystem: System = defineSystem({
  name: 'GhostVisualSystem',
  group: 'draw',
  after: ['VehicleVisualSystem'],

  update(state: State) {
    if (state.headless) return;
    const scene = getScene(state);
    if (!scene) return;

    const race = getRaceState();
    const player = playerQuery(state.world)[0];
    const trackEid = trackQuery(state.world)[0];
    const spline =
      trackEid !== undefined ? getTrackSpline(trackEid) : undefined;
    const show =
      spline !== undefined &&
      player !== undefined &&
      (race.phase === 'countdown' || race.phase === 'racing') &&
      sampleGhostAtTime(0) !== null;

    if (!show) {
      if (visual) visual.group.visible = false;
      return;
    }

    if (!visual) {
      visual = buildGhost();
      scene.add(visual.group);
    }

    const lapTime =
      race.phase === 'countdown'
        ? 0
        : race.raceTime - RaceTracker.lapStartTime[player];
    const sample = sampleGhostAtTime(lapTime);
    if (!sample) {
      visual.group.visible = false;
      return;
    }

    const pose = ghostWorldPose(spline, sample, RIDE_HEIGHT);
    visual.group.visible = true;
    visual.group.position.set(pose.x, pose.y, pose.z);
    visual.group.quaternion.set(pose.qx, pose.qy, pose.qz, pose.qw);

    const mat = visual.hull.material as THREE.MeshStandardMaterial;
    mat.opacity = 0.32 + Math.sin(state.time.elapsed * 6) * 0.06;
  },

  dispose() {
    if (!visual) return;
    visual.group.parent?.remove(visual.group);
    visual.group.traverse((obj) => {
      const mesh = obj as THREE.Mesh;
      if (mesh.isMesh) {
        mesh.geometry?.dispose();
        const mat = mesh.material as THREE.Material | THREE.Material[];
        if (Array.isArray(mat)) mat.forEach((m) => m.dispose());
        else mat?.dispose();
      }
    });
    visual = null;
  },
});

function buildGhost(): GhostVisual {
  const group = new THREE.Group();
  group.name = 'GhostKart';
  const mat = new THREE.MeshStandardMaterial({
    color: GHOST_COLOR,
    emissive: GHOST_COLOR,
    emissiveIntensity: 0.55,
    transparent: true,
    opacity: 0.34,
    depthWrite: false,
    roughness: 0.35,
    metalness: 0.15,
  });
  const hull = new THREE.Mesh(new THREE.BoxGeometry(1.45, 0.42, 2.9), mat);
  hull.position.y = 0.22;
  group.add(hull);
  const nose = new THREE.Mesh(new THREE.BoxGeometry(1.05, 0.24, 0.8), mat);
  nose.position.set(0, 0.14, 1.55);
  group.add(nose);
  const wing = new THREE.Mesh(new THREE.BoxGeometry(1.5, 0.07, 0.36), mat);
  wing.position.set(0, 0.55, -1.45);
  group.add(wing);
  return { group, hull };
}
