import * as THREE from 'three';
import { defineSystem, defineQuery, type State, type System } from '../../core';
import { getScene } from '../rendering';
import { Track } from './components';
import { getTrackSpline } from './data';
import { GRID_FIRST_S } from './race-director';
import { getRaceState, type RacePhase } from './race-state';
import type { TrackSpline } from './spline';

const trackQuery = defineQuery([Track]);

/** Gantry sits this far past pole so the grid looks at the lights. */
const LIGHTS_AHEAD_S = 12;
const POST_H = 5.4;
export const START_LIGHT_COUNT = 3;

interface Gantry {
  group: THREE.Group;
  mats: THREE.MeshStandardMaterial[];
}

const gantries = new Map<number, Gantry>();

function makeMat(color: number): THREE.MeshStandardMaterial {
  return new THREE.MeshStandardMaterial({
    color,
    roughness: 0.45,
    metalness: 0.35,
    emissive: 0x000000,
    emissiveIntensity: 0,
  });
}

function buildGantry(spline: TrackSpline, s: number, halfW: number): Gantry {
  const group = new THREE.Group();
  group.name = 'StartLights';
  const steel = makeMat(0x2a2e36);
  const left = spline.positionAt(s, -halfW, 0);
  const right = spline.positionAt(s, halfW, 0);
  const postGeo = new THREE.BoxGeometry(0.32, POST_H, 0.32);
  for (const foot of [left, right]) {
    const post = new THREE.Mesh(postGeo, steel);
    post.position.set(foot.x, foot.y + POST_H * 0.5, foot.z);
    group.add(post);
  }
  const dx = right.x - left.x;
  const dy = right.y - left.y;
  const dz = right.z - left.z;
  const span = Math.hypot(dx, dy, dz) || 1;
  const beam = new THREE.Mesh(
    new THREE.BoxGeometry(span + 0.4, 0.28, 0.36),
    steel
  );
  beam.position.set(
    (left.x + right.x) * 0.5,
    (left.y + right.y) * 0.5 + POST_H,
    (left.z + right.z) * 0.5
  );
  beam.lookAt(right.x, right.y + POST_H, right.z);
  beam.rotateY(Math.PI / 2);
  group.add(beam);

  const mats: THREE.MeshStandardMaterial[] = [];
  const bulbGeo = new THREE.SphereGeometry(0.22, 12, 10);
  for (let i = 0; i < START_LIGHT_COUNT; i++) {
    const t = (i + 1) / (START_LIGHT_COUNT + 1);
    const mat = makeMat(0x4a1010);
    mats.push(mat);
    const bulb = new THREE.Mesh(bulbGeo, mat);
    bulb.position.set(
      left.x + dx * t,
      left.y + dy * t + POST_H - 0.55,
      left.z + dz * t
    );
    group.add(bulb);
  }
  return { group, mats };
}

function setBulb(
  mat: THREE.MeshStandardMaterial,
  on: boolean,
  green: boolean
): void {
  if (!on) {
    mat.color.setHex(0x2a1212);
    mat.emissive.setHex(0x000000);
    mat.emissiveIntensity = 0;
    return;
  }
  if (green) {
    mat.color.setHex(0x1a3a22);
    mat.emissive.setHex(0x3dff7a);
    mat.emissiveIntensity = 4.2;
  } else {
    mat.color.setHex(0x3a1212);
    mat.emissive.setHex(0xff2a2a);
    mat.emissiveIntensity = 3.6;
  }
}

/** How many bulbs are on, and whether they are the green GO row. */
export function startLightPattern(
  phase: RacePhase,
  countdown: number,
  raceTime: number
): { lit: number; green: boolean } {
  if (phase === 'countdown') {
    const n = Math.ceil(countdown);
    return {
      lit: Math.max(0, Math.min(START_LIGHT_COUNT, 4 - n)),
      green: false,
    };
  }
  if (phase === 'racing' && raceTime < 0.85) {
    return { lit: START_LIGHT_COUNT, green: true };
  }
  return { lit: 0, green: false };
}

/**
 * Physical start lights over the grid. HUD already shows 3-2-1-GO; this is the
 * same clock as a gantry the cars actually look at.
 */
export const StartLightsSystem: System = defineSystem({
  name: 'StartLightsSystem',
  group: 'draw',
  // TrackSpawnSystem is `simulation`. Draw already runs after that group in
  // the same frame; a cross-group `after` is rejected by the scheduler.

  update(state: State) {
    if (state.headless) return;
    const scene = getScene(state) as THREE.Scene | null;
    if (!scene) return;
    const race = getRaceState();

    for (const eid of trackQuery(state.world)) {
      let gantry = gantries.get(eid);
      if (!gantry) {
        const spline = getTrackSpline(eid);
        if (!spline) continue;
        const half = (Track.width[eid] || 12) * 0.5 + 1.15;
        gantry = buildGantry(spline, GRID_FIRST_S + LIGHTS_AHEAD_S, half);
        scene.add(gantry.group);
        gantries.set(eid, gantry);
      }
      const { lit, green } = startLightPattern(
        race.phase,
        race.countdown,
        race.raceTime
      );
      for (let i = 0; i < gantry.mats.length; i++) {
        setBulb(gantry.mats[i]!, i < lit, green);
      }
    }
  },

  dispose() {
    for (const g of gantries.values()) {
      g.group.removeFromParent();
      const geos = new Set<THREE.BufferGeometry>();
      const mats = new Set<THREE.Material>();
      g.group.traverse((o) => {
        const mesh = o as THREE.Mesh;
        if (!mesh.isMesh) return;
        geos.add(mesh.geometry);
        const mat = mesh.material;
        if (Array.isArray(mat)) mat.forEach((m) => mats.add(m));
        else mats.add(mat);
      });
      geos.forEach((geo) => geo.dispose());
      mats.forEach((m) => m.dispose());
    }
    gantries.clear();
  },
});
