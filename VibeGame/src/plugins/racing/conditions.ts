import * as THREE from 'three';
import { defineSystem, defineQuery, type State, type System } from '../../core';
import { getScene } from '../rendering';
import { setWeather } from '../weather';
import { Track } from './components';
import { getTrackMeshes } from './track-spawn';
import {
  conditionIsNight,
  conditionWetness,
  getRaceState,
  type TrackCondition,
} from './race-state';

const trackQuery = defineQuery([Track]);

const lightOrig = new WeakMap<THREE.Light, number>();
let fogOrig: { near: number; far: number; color: number } | null = null;
let lastStamp = '';

function origIntensity(light: THREE.Light): number {
  const stored = lightOrig.get(light);
  if (stored !== undefined) return stored;
  lightOrig.set(light, light.intensity);
  return light.intensity;
}

function applyAtmosphere(scene: THREE.Scene, condition: TrackCondition): void {
  const night = conditionIsNight(condition);
  const wet = conditionWetness(condition) > 0.5;
  const dirScale = night ? (wet ? 0.12 : 0.22) : wet ? 0.55 : 1;
  const hemiScale = night ? (wet ? 0.18 : 0.28) : wet ? 0.7 : 1;

  scene.traverse((obj) => {
    const light = obj as THREE.Light;
    if (!light.isLight) return;
    if ((light as THREE.SpotLight).isSpotLight) return;
    if ((light as THREE.PointLight).isPointLight) return;
    const base = origIntensity(light);
    if ((light as THREE.DirectionalLight).isDirectionalLight) {
      light.intensity = base * dirScale;
    } else if ((light as THREE.HemisphereLight).isHemisphereLight) {
      light.intensity = base * hemiScale;
    } else if ((light as THREE.AmbientLight).isAmbientLight) {
      light.intensity = base * hemiScale;
    }
  });

  if (scene.fog instanceof THREE.Fog) {
    if (!fogOrig) {
      fogOrig = {
        near: scene.fog.near,
        far: scene.fog.far,
        color: scene.fog.color.getHex(),
      };
    }
    if (night) {
      scene.fog.near = 36;
      scene.fog.far = 260;
      scene.fog.color.setHex(0x070b14);
    } else if (wet) {
      scene.fog.near = Math.min(fogOrig.near, 90);
      scene.fog.far = Math.min(fogOrig.far, 560);
      scene.fog.color.setHex(0x4a5568);
    } else {
      scene.fog.near = fogOrig.near;
      scene.fog.far = fogOrig.far;
      scene.fog.color.setHex(fogOrig.color);
    }
  }
}

function applyWetRoad(eid: number, wet: boolean): void {
  const meshes = getTrackMeshes(eid);
  if (!meshes) return;
  const mat = meshes.road.material as THREE.MeshStandardMaterial;
  if (mat.userData?.holo) return;
  mat.roughness = wet ? 0.16 : 0.62;
  mat.metalness = wet ? 0.42 : 0.02;
  mat.envMapIntensity = wet ? 1.4 : 1;
}

/**
 * Pushes the chosen {@link TrackCondition} into weather, lighting, fog and
 * the asphalt. Headless skips the visual half; grip still reads the condition
 * from race state.
 */
export const RaceConditionsSystem: System = defineSystem({
  name: 'RaceConditionsSystem',
  group: 'draw',
  // TrackSpawnSystem is `simulation`; draw already runs after that group.
  // Cross-group `after` is rejected by the scheduler. Weather is draw.
  after: ['WeatherSystem'],

  update(state: State) {
    const condition = getRaceState().condition;
    const wet = conditionWetness(condition);
    const night = conditionIsNight(condition);

    if (!state.headless) {
      setWeather(state, {
        rain: wet,
        clouds: wet ? 0.92 : night ? 0.5 : 0.35,
        windStrength: wet ? 3.4 : 1.6,
        fadeSeconds: lastStamp.startsWith(`${condition}|`) ? 2.5 : 0.35,
      });
    }

    if (state.headless) return;
    const scene = getScene(state) as THREE.Scene | null;
    const tracks = trackQuery(state.world);
    let meshesReady = 0;
    for (const eid of tracks) {
      if (getTrackMeshes(eid)) meshesReady++;
    }
    const stamp = `${condition}|${scene ? 1 : 0}|${meshesReady}`;
    if (stamp === lastStamp) return;
    lastStamp = stamp;
    if (scene) applyAtmosphere(scene, condition);
    for (const eid of tracks) applyWetRoad(eid, wet > 0.5);
  },

  dispose() {
    lastStamp = '';
    fogOrig = null;
  },
});
