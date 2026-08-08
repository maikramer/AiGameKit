import * as THREE from 'three';
import { defineSystem, defineQuery, type State, type System } from '../../core';
import { getScene } from '../rendering';
import { Track } from './components';
import { getTrackData } from './data';
import { buildTrackMeshes, type TrackMeshes } from './track-geometry';

const trackSpawnQuery = defineQuery([Track]);
const built = new Map<number, TrackMeshes & { lights?: THREE.Group }>();

/**
 * Lazily builds and mounts the track geometry + street lights the first frame a
 * `<Track>` entity exists with centerline data. Runs in `simulation` so the
 * meshes are in the scene before the camera's first draw.
 * Idempotent: only builds once per track entity.
 *
 * Street lights: placed along both edges of the track at regular intervals,
 * casting colored light onto the road surface (NFS night-race aesthetic).
 */
export const TrackSpawnSystem: System = defineSystem({
  name: 'TrackSpawnSystem',
  group: 'simulation',

  update(state: State) {
    if (state.headless) return;
    const scene = getScene(state);
    if (!scene) return;
    const tracks = trackSpawnQuery(state.world);
    for (const eid of tracks) {
      if (built.has(eid)) continue;
      const data = getTrackData(state, eid);
      if (!data || data.centerline.length < 4) continue;
      const meshes = buildTrackMeshes(data);

      // --- Add street lights along the track -------------------------------
      const lights = buildStreetLights(data);
      meshes.group.add(lights);
      (meshes as TrackMeshes & { lights?: THREE.Group }).lights = lights;

      scene.add(meshes.group);
      built.set(eid, meshes as TrackMeshes & { lights?: THREE.Group });

      // Expose centerline for HUD minimap radar.
      (state as any).__trackCenterline = data.centerline;
    }
  },

  dispose() {
    for (const m of built.values()) {
      if (m.lights) {
        m.lights.traverse((obj) => {
          if ((obj as THREE.Light).isLight) (obj as THREE.Light).dispose();
          if ((obj as THREE.Mesh).isMesh) {
            (obj as THREE.Mesh).geometry?.dispose();
            ((obj as THREE.Mesh).material as THREE.Material)?.dispose();
          }
        });
      }
      m.dispose();
    }
    built.clear();
  },
});

// ---- Street Light Builder --------------------------------------------------

function buildStreetLights(track: { centerline: number[]; halfWidth: number; closed: boolean; elevations?: number[] }): THREE.Group {
  const group = new THREE.Group();
  group.name = 'StreetLights';

  const { centerline, halfWidth } = track;
  const n = Math.floor(centerline.length / 2);
  const elevations = track.elevations ?? [];
  const hasElev = elevations.length === n && n > 0;
  const pts: THREE.Vector3[] = [];
  for (let i = 0; i < n; i++) {
    pts.push(new THREE.Vector3(centerline[i * 2]!, hasElev ? elevations[i]! : 0, centerline[i * 2 + 1]!));
  }

  // Place lights every ~15m along each edge (3D arc length so hills don't
  // crowd lights on descents).
  const spacing = 15;
  let accumulatedDist = 0;

  // Color palette for lights (alternating cyan / magenta / warm white).
  const colors = [
    { light: 0x00ccff, intensity: 3, distance: 25 },  // cyan neon
    { light: 0xff00aa, intensity: 2.5, distance: 22 }, // magenta neon
    { light: 0xffeecc, intensity: 2, distance: 20 },   // warm white
  ];

  for (let i = 1; i < pts.length; i++) {
    const prev = pts[i - 1]!;
    const curr = pts[i]!;
    const segLen = prev.distanceTo(curr);
    accumulatedDist += segLen;

    if (accumulatedDist >= spacing) {
      accumulatedDist -= spacing;

      // Tangent and normal.
      const tx = curr.x - prev.x;
      const tz = curr.z - prev.z;
      const tl = Math.hypot(tx, tz) || 1;
      const nx = -tz / tl;
      const nz = tx / tl;

      // Place on both sides.
      for (const side of [-1, 1]) {
        const colorIdx = (i + Math.max(0, side)) % colors.length;
        const c = colors[colorIdx];

        const lx = curr.x + nx * (halfWidth + 1.2) * side;
        const lz = curr.z + nz * (halfWidth + 1.2) * side;
        const ly = curr.y; // pole base rides the track elevation

        // Pole.
        const poleGeo = new THREE.CylinderGeometry(0.04, 0.06, 5.5, 6);
        poleGeo.translate(0, 2.75, 0);
        const poleMat = new THREE.MeshStandardMaterial({
          color: 0x222228,
          roughness: 0.6,
          metalness: 0.4,
        });
        const pole = new THREE.Mesh(poleGeo, poleMat);
        pole.position.set(lx, ly, lz);
        pole.castShadow = true;
        group.add(pole);

        // Lamp head (arm extending over the road).
        const armGeo = new THREE.BoxGeometry(0.8, 0.04, 0.04);
        const armMat = new THREE.MeshStandardMaterial({ color: 0x333338, metalness: 0.5 });
        const arm = new THREE.Mesh(armGeo, armMat);
        arm.position.set(lx - nx * 0.4 * side, ly + 5.3, lz - nz * 0.4 * side);
        arm.rotation.y = Math.atan2(nx, nz) + (side > 0 ? Math.PI : 0);
        group.add(arm);

        // Light fixture (glowing box).
        const fixtureGeo = new THREE.BoxGeometry(0.25, 0.12, 0.12);
        const fixtureMat = new THREE.MeshStandardMaterial({
          color: c.light,
          emissive: c.light,
          emissiveIntensity: 2.0,
        });
        const fixture = new THREE.Mesh(fixtureGeo, fixtureMat);
        fixture.position.set(
          lx - nx * 0.8 * side,
          ly + 5.2,
          lz - nz * 0.8 * side
        );
        group.add(fixture);

        // Actual PointLight.
        const pointLight = new THREE.PointLight(c.light, c.intensity, c.distance);
        pointLight.position.copy(fixture.position);
        pointLight.castShadow = false; // performance: no shadow from street lights
        group.add(pointLight);
      }
    }
  }

  return group;
}

