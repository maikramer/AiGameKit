import * as THREE from 'three';
import { defineSystem, defineQuery, type State, type System } from '../../core';
import { getScene } from '../rendering';
import { Track, TrackObstacleState } from './components';
import { getTrackSpline } from './data';
import type { TrackSpline } from './spline';

/**
 * Visual representation of track-side obstacles.
 *
 * The obstacle physics is a circle test in `data.ts` (via `addTrackObstacle`
 * world XZ); this system renders the mesh per kind and syncs it to the
 * spline position every draw frame. The sidewinder moves the *track-space*
 * record, so the visual simply re-samples the spline at the updated arc —
 * no extra state.
 */

const _pos = { x: 0, y: 0, z: 0 };

const KINDS = ['Barrel', 'Drone', 'Gate'] as const;

function buildBarrel(): THREE.Group {
  const g = new THREE.Group();
  const body = new THREE.Mesh(
    new THREE.CylinderGeometry(0.7, 0.62, 1.4, 12),
    new THREE.MeshStandardMaterial({ color: 0x8a2be2, roughness: 0.5, metalness: 0.3 })
  );
  body.position.y = 0.7;
  g.add(body);
  const band1 = new THREE.Mesh(
    new THREE.CylinderGeometry(0.72, 0.72, 0.12, 12),
    new THREE.MeshStandardMaterial({ color: 0x38e8ff, emissive: 0x38e8ff, emissiveIntensity: 1.2 })
  );
  band1.position.y = 0.5;
  const band2 = band1.clone();
  band2.position.y = 1.0;
  g.add(band1, band2);
  return g;
}

function buildDrone(): THREE.Group {
  const g = new THREE.Group();
  const ring = new THREE.Mesh(
    new THREE.TorusGeometry(0.9, 0.12, 10, 24),
    new THREE.MeshStandardMaterial({ color: 0x2b2b3a, roughness: 0.6, metalness: 0.5 })
  );
  g.add(ring);
  const core = new THREE.Mesh(
    new THREE.SphereGeometry(0.4, 12, 10),
    new THREE.MeshStandardMaterial({
      color: 0xff5dff,
      emissive: 0xff5dff,
      emissiveIntensity: 1.8,
    })
  );
  core.position.y = 0;
  g.add(core);
  return g;
}

function buildGate(): THREE.Group {
  const g = new THREE.Group();
  const mat = new THREE.MeshStandardMaterial({
    color: 0x11151d,
    roughness: 0.5,
    metalness: 0.6,
  });
  const glow = new THREE.MeshStandardMaterial({
    color: 0x38e8ff,
    emissive: 0x38e8ff,
    emissiveIntensity: 1.6,
  });
  // Two pillars + a lintel + glowing strips.
  for (const side of [-1, 1]) {
    const pillar = new THREE.Mesh(new THREE.BoxGeometry(0.24, 2.4, 0.24), mat);
    pillar.position.set(side * 0.9, 1.2, 0);
    g.add(pillar);
    const strip = new THREE.Mesh(new THREE.BoxGeometry(0.06, 1.8, 0.06), glow);
    strip.position.set(side * 0.9, 1.2, 0.1);
    g.add(strip);
  }
  const lintel = new THREE.Mesh(new THREE.BoxGeometry(2.04, 0.24, 0.24), mat);
  lintel.position.y = 2.4;
  g.add(lintel);
  const topGlow = new THREE.Mesh(new THREE.BoxGeometry(1.8, 0.06, 0.06), glow);
  topGlow.position.y = 2.4;
  topGlow.position.z = 0.1;
  g.add(topGlow);
  return g;
}

function buildObstacle(kind: number): THREE.Group {
  const g = new THREE.Group();
  g.name = `Obstacle:${KINDS[kind] ?? 'Unknown'}`;
  if (kind === 0) {
    g.add(buildBarrel());
  } else if (kind === 1) {
    g.add(buildDrone());
  } else {
    g.add(buildGate());
  }
  return g;
}

interface ObstacleVisual {
  group: THREE.Group;
  kind: number;
  eid: number;
}

const visuals: ObstacleVisual[] = [];

const obstacleQuery = defineQuery([TrackObstacleState]);
const trackQuery = defineQuery([Track]);

export const TrackObstacleVisualSystem: System = defineSystem({
  name: 'TrackObstacleVisualSystem',
  group: 'draw',

  update(state: State) {
    if (state.headless) return;
    const scene = getScene(state) as THREE.Scene | null;
    if (!scene) return;
    const trackEid = trackQuery(state.world)[0];
    if (trackEid === undefined) return;
    const spline = getTrackSpline(trackEid);
    if (!spline) return;
    const entities = obstacleQuery(state.world);
    const t = state.time.elapsed;

    // Build visuals for any entity that lacks one.
    for (const eid of entities) {
      if (!visuals.some((v) => v.eid === eid)) {
        const kind = TrackObstacleState.kind[eid] ?? 0;
        const group = buildObstacle(kind);
        scene.add(group);
        visuals.push({ group, kind, eid });
      }
    }
    // Remove visuals whose entity is gone.
    for (let i = visuals.length - 1; i >= 0; i--) {
      if (!entities.includes(visuals[i]!.eid)) {
        visuals[i]!.group.parent?.remove(visuals[i]!.group);
        visuals.splice(i, 1);
      }
    }

    for (const eid of entities) {
      const v = visuals.find((x) => x.eid === eid);
      if (!v) continue;
      const sReal = TrackObstacleState.s[eid] ?? 0;
      const lateral = TrackObstacleState.lateral[eid] ?? 0;
      spline.positionAt(sReal, lateral, 0, _pos);
      const spin = TrackObstacleState.spin[eid] ?? 0;
      const hover = TrackObstacleState.hover[eid] ?? 0;
      v.group.position.set(_pos.x, _pos.y + hover + Math.sin(t * 2 + eid) * 0.15, _pos.z);
      v.group.rotation.y = t * spin + eid;
    }
  },

  dispose() {
    for (const v of visuals) {
      v.group.parent?.remove(v.group);
      v.group.traverse((o) => {
        const mesh = o as THREE.Mesh;
        if (mesh.isMesh) {
          mesh.geometry?.dispose();
          (mesh.material as THREE.Material)?.dispose();
        }
      });
    }
    visuals.length = 0;
  },
});

/** Get the active obstacle visual groups (tests / debug). */
export function getObstacleVisuals(): readonly ObstacleVisual[] {
  return visuals;
}

/** Resolve an obstacle's world position (for the sidewinder). */
export function obstacleWorldPos(
  spline: TrackSpline,
  s: number,
  lateral: number,
  out = { x: 0, y: 0, z: 0 }
): { x: number; y: number; z: number } {
  return spline.positionAt(s, lateral, 0, out);
}

/** Shared position object for the sidewinder probe. */
export function obstacleProbe(): { x: number; y: number; z: number } {
  return _pos;
}
