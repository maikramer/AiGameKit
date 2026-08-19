import * as THREE from 'three';
import { defineSystem, defineQuery, type State, type System } from '../../core';
import { getScene } from '../rendering';
import { Track, TrackObstacleState } from './components';
import {
  getTrackSpline,
  getTrackSpaceObstacles,
  repositionTrackObstacle,
  type TrackSpline,
} from './data';

/**
 * Track-side obstacles: visuals per kind plus the movement system. The physics
 * is a circle test in `data.ts` (world XZ); this module renders the mesh per
 * kind and, for moving hazards, advances the track-space record and keeps the
 * world spatial hash in sync so collision queries see the new spot.
 */

const _pos = { x: 0, y: 0, z: 0 };

const KINDS = ['Barrel', 'Drone', 'Gate', 'Crate'] as const;

function buildBarrel(): THREE.Group {
  const g = new THREE.Group();
  const body = new THREE.Mesh(
    new THREE.CylinderGeometry(0.7, 0.62, 1.4, 12),
    new THREE.MeshStandardMaterial({
      color: 0x8a2be2,
      roughness: 0.5,
      metalness: 0.3,
    })
  );
  body.position.y = 0.7;
  g.add(body);
  const band1 = new THREE.Mesh(
    new THREE.CylinderGeometry(0.72, 0.72, 0.12, 12),
    new THREE.MeshStandardMaterial({
      color: 0x38e8ff,
      emissive: 0x38e8ff,
      emissiveIntensity: 1.2,
    })
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
    new THREE.MeshStandardMaterial({
      color: 0x2b2b3a,
      roughness: 0.6,
      metalness: 0.5,
    })
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

function buildCrate(): THREE.Group {
  const g = new THREE.Group();
  const wood = new THREE.MeshStandardMaterial({
    color: 0x9c7a4a,
    roughness: 0.85,
    metalness: 0.02,
  });
  const edge = new THREE.MeshStandardMaterial({
    color: 0x5c4126,
    roughness: 0.7,
  });
  const body = new THREE.Mesh(new THREE.BoxGeometry(1.5, 1.2, 1.5), wood);
  body.position.y = 0.6;
  g.add(body);
  for (const y of [0.18, 1.02]) {
    const band = new THREE.Mesh(new THREE.BoxGeometry(1.56, 0.14, 1.56), edge);
    band.position.y = y;
    g.add(band);
  }
  return g;
}

function buildObstacle(kind: number): THREE.Group {
  const g = new THREE.Group();
  g.name = `Obstacle:${KINDS[kind] ?? 'Unknown'}`;
  if (kind === 0) {
    g.add(buildBarrel());
  } else if (kind === 1) {
    g.add(buildDrone());
  } else if (kind === 3) {
    g.add(buildCrate());
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

/**
 * MovingObstacleSystem — advances sweeping/travelling hazards and re-forms
 * broken crates. Sweep oscillates the lateral offset around its rest value;
 * travel marches the arc position forward and wraps at the finish line.
 */
export const MovingObstacleSystem: System = defineSystem({
  name: 'MovingObstacleSystem',
  group: 'fixed',

  update(state: State) {
    const trackEid = trackQuery(state.world)[0];
    if (trackEid === undefined) return;
    const spline = getTrackSpline(trackEid);
    if (!spline) return;
    const dt = state.time.fixedDeltaTime;

    for (const o of getTrackSpaceObstacles()) {
      if (o.moveMode === 1) {
        o.movePhase += o.moveSpeed * dt;
        o.lateral = o.baseLateral + Math.sin(o.movePhase) * o.moveRange;
      } else if (o.moveMode === 2) {
        o.s = spline.wrapS(o.s + o.moveSpeed * dt);
      } else {
        continue;
      }
      const p = spline.positionAt(o.s, o.lateral);
      if (o.worldIndex >= 0) repositionTrackObstacle(o.worldIndex, p.x, p.z);
      if (o.eid >= 0) {
        TrackObstacleState.s[o.eid] = o.s;
        TrackObstacleState.lateral[o.eid] = o.lateral;
      }
    }

    // Crates re-form a few seconds after shattering.
    for (const eid of obstacleQuery(state.world)) {
      const cd = TrackObstacleState.cooldown[eid] ?? 0;
      if (cd > 0) TrackObstacleState.cooldown[eid] = Math.max(0, cd - dt);
    }
  },
});

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
        disposeGroup(visuals[i]!.group);
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
      v.group.position.set(
        _pos.x,
        _pos.y + hover + Math.sin(t * 2 + eid) * 0.15,
        _pos.z
      );
      v.group.rotation.y = t * spin + eid;
      // A shattered crate is a puddle of debris until it re-forms.
      v.group.visible = (TrackObstacleState.cooldown[eid] ?? 0) <= 0;
      if (!v.group.visible) continue;
      const pop = TrackObstacleState.cooldown[eid] ?? 0;
      if (pop > 0 && pop < 0.5) {
        v.group.scale.setScalar(0.2 + (1 - pop / 0.5) * 0.8);
      } else {
        v.group.scale.setScalar(1);
      }
    }
  },

  dispose() {
    for (const v of visuals) {
      v.group.parent?.remove(v.group);
      disposeGroup(v.group);
    }
    visuals.length = 0;
  },
});

function disposeGroup(group: THREE.Group): void {
  group.traverse((o) => {
    const mesh = o as THREE.Mesh;
    if (mesh.isMesh) {
      mesh.geometry?.dispose();
      (mesh.material as THREE.Material)?.dispose();
    }
  });
}

/** Get the active obstacle visual groups (tests / debug). */
export function getObstacleVisuals(): readonly ObstacleVisual[] {
  return visuals;
}

/** Resolve an obstacle's world position (for FX placement). */
export function obstacleWorldPos(
  spline: TrackSpline,
  s: number,
  lateral: number,
  out = { x: 0, y: 0, z: 0 }
): { x: number; y: number; z: number } {
  return spline.positionAt(s, lateral, 0, out);
}
