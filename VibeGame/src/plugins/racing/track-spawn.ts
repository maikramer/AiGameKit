import type * as THREE from 'three';
import { defineSystem, defineQuery, type State, type System } from '../../core';
import { getScene } from '../rendering';
import { Track } from './components';
import { getTrackSpline } from './data';
import {
  buildTrackMeshes,
  type TrackMeshes,
  type TrackStyle,
} from './track-geometry';
const trackQuery = defineQuery([Track]);
const built = new Map<number, TrackMeshes>();

/** Per-track visual style, set by the `<RaceTrack>` parser. */
export const trackStyles = new Map<number, TrackStyle>();

/**
 * Mounts the circuit geometry the first frame a `<RaceTrack>` has a spline.
 *
 * Runs in `simulation` so the meshes are in the scene before the first draw,
 * and is idempotent — one build per track entity, no per-frame work afterwards.
 */
export const TrackSpawnSystem: System = defineSystem({
  name: 'TrackSpawnSystem',
  group: 'simulation',

  update(state: State) {
    if (state.headless) return;
    const scene = getScene(state) as THREE.Scene | null;
    if (!scene) return;

    for (const eid of trackQuery(state.world)) {
      if (built.has(eid)) continue;
      const spline = getTrackSpline(eid);
      if (!spline) continue;
      const meshes = buildTrackMeshes(
        spline,
        Track.shoulder[eid] || 0,
        trackStyles.get(eid) ?? {}
      );
      scene.add(meshes.group);
      built.set(eid, meshes);
      Track.length[eid] = spline.length;
    }
  },

  dispose() {
    for (const meshes of built.values()) {
      meshes.group.parent?.remove(meshes.group);
      meshes.dispose();
    }
    built.clear();
    trackStyles.clear();
  },
});

/** The built meshes for a track entity (tests / debug tooling). */
export function getTrackMeshes(entity: number): TrackMeshes | undefined {
  return built.get(entity);
}

/**
 * Holo surface pulse: slowly breathes the emissive intensity of every holo
 * material on the track so the grid reads as alive rather than static paint.
 * Only touches materials flagged `userData.holo`, so an asphalt track costs
 * nothing.
 */
export const HoloPulseSystem: System = defineSystem({
  name: 'HoloPulseSystem',
  group: 'draw',

  update(state: State) {
    if (state.headless) return;
    const t = state.time.elapsed;
    const intensity = 0.45 + Math.sin(t * 1.6) * 0.18;
    for (const meshes of built.values()) {
      meshes.group.traverse((obj) => {
        const mesh = obj as THREE.Mesh;
        if (!mesh.isMesh) return;
        const mat = mesh.material as THREE.MeshStandardMaterial | null;
        if (!mat || !mat.userData?.holo) return;
        mat.emissiveIntensity = intensity;
      });
    }
  },
});
