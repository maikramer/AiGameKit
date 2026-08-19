import * as THREE from 'three';
import { defineSystem, defineQuery, type State, type System } from '../../core';
import { getScene } from '../rendering';
import { Track } from './components';
import { getTrackSpline, getTrackRamps, type TrackRamp } from './data';
import { createFrame } from './spline';

/**
 * Jump ramp visuals. A ramp is a wedge spanning `length` metres of track: the
 * vehicle controller climbs the same linear profile (`rampHeightAt`), so what
 * the player sees is exactly what the car drives over. The wedge gets glowing
 * lip stripes so the take-off reads at racing speed.
 */

interface RampVisual {
  group: THREE.Group;
}

const visuals: RampVisual[] = [];
const trackQuery = defineQuery([Track]);
const _frame = createFrame();

function buildWedge(ramp: TrackRamp): THREE.Group {
  const g = new THREE.Group();
  g.name = 'RampWedge';
  const w = ramp.width;
  const len = ramp.length;
  const h = ramp.height;
  const hw = w * 0.5;

  // Triangular prism: sloped deck from (z=0, y=0) up to the lip (z=len, y=h),
  // closed at the back and on the sides; the bottom sits on the road.
  const geo = new THREE.BufferGeometry();
  const verts = new Float32Array([
    // Sloped deck (two triangles)
    -hw,
    0,
    0,
    hw,
    0,
    0,
    hw,
    h,
    len,
    -hw,
    0,
    0,
    hw,
    h,
    len,
    -hw,
    h,
    len,
    // Back face
    -hw,
    0,
    len,
    hw,
    0,
    len,
    hw,
    h,
    len,
    -hw,
    0,
    len,
    hw,
    h,
    len,
    -hw,
    h,
    len,
    // Sides
    -hw,
    0,
    0,
    -hw,
    h,
    len,
    -hw,
    0,
    len,
    hw,
    0,
    0,
    hw,
    0,
    len,
    hw,
    h,
    len,
  ]);
  geo.setAttribute('position', new THREE.BufferAttribute(verts, 3));
  geo.computeVertexNormals();
  const deck = new THREE.Mesh(
    geo,
    new THREE.MeshStandardMaterial({
      color: 0x5a4632,
      roughness: 0.85,
      metalness: 0.05,
    })
  );
  g.add(deck);

  // Glowing lip stripes along both sloped edges — the visual cue for "jump".
  const stripMat = new THREE.MeshStandardMaterial({
    color: 0xffc35c,
    emissive: 0xffc35c,
    emissiveIntensity: 1.8,
  });
  const stripLen = Math.hypot(len, h);
  const stripPitch = Math.atan2(h, len);
  for (const side of [-1, 1]) {
    const strip = new THREE.Mesh(
      new THREE.BoxGeometry(0.16, 0.06, stripLen),
      stripMat
    );
    strip.position.set(side * (hw - 0.02), h * 0.5 + 0.03, len * 0.5);
    strip.rotation.x = -stripPitch;
    g.add(strip);
  }
  // Chevron bars across the deck, so the slope direction is obvious.
  const chevMat = new THREE.MeshStandardMaterial({
    color: 0x38e8ff,
    emissive: 0x38e8ff,
    emissiveIntensity: 1.4,
  });
  for (let i = 1; i <= 3; i++) {
    const t = i / 4;
    const chev = new THREE.Mesh(
      new THREE.BoxGeometry(w - 0.5, 0.04, 0.3),
      chevMat
    );
    chev.position.set(0, t * h + 0.04, t * len);
    chev.rotation.x = -stripPitch;
    g.add(chev);
  }
  return g;
}

export const RampVisualSystem: System = defineSystem({
  name: 'RampVisualSystem',
  group: 'draw',

  update(state: State) {
    if (state.headless) return;
    const scene = getScene(state) as THREE.Scene | null;
    if (!scene) return;
    const trackEid = trackQuery(state.world)[0];
    if (trackEid === undefined) return;
    const spline = getTrackSpline(trackEid);
    if (!spline) return;
    const ramps = getTrackRamps();

    while (visuals.length > ramps.length) {
      const v = visuals.pop()!;
      v.group.parent?.remove(v.group);
      disposeGroup(v.group);
    }
    while (visuals.length < ramps.length) {
      const group = buildWedge(ramps[visuals.length]!);
      scene.add(group);
      visuals.push({ group });
    }

    for (let i = 0; i < ramps.length; i++) {
      const ramp = ramps[i]!;
      const v = visuals[i]!;
      spline.sampleAt(ramp.s + ramp.length * 0.5, _frame);
      v.group.position.set(
        _frame.x + _frame.rx * ramp.lateral,
        _frame.y + _frame.uy * 0.02,
        _frame.z + _frame.rz * ramp.lateral
      );
      // The wedge extends along local +Z; align that with the track tangent.
      v.group.rotation.y = Math.atan2(_frame.tx, _frame.tz);
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
