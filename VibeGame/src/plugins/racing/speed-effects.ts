import * as THREE from 'three';
import { defineSystem, defineQuery, type State, type System } from '../../core';
import { getScene } from '../rendering';
import { Vehicle } from './components';

const vehicleQuery = defineQuery([Vehicle]);

// Per-vehicle effect state.
const effectState = new Map<number, SpeedEffectState>();

interface SpeedEffectState {
  speedLines: THREE.Group;
  lineMeshes: THREE.Mesh[];
  vignette: THREE.Mesh;
  chromaticMat: THREE.ShaderMaterial | null;
  active: boolean;
}

// ---- Speed Lines Shader ---------------------------------------------------

const SPEED_LINE_VERT = `
attribute float aAlpha;
varying float vAlpha;
void main() {
  vAlpha = aAlpha;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`;

const SPEED_LINE_FRAG = `
varying float vAlpha;
uniform vec3 uColor;
uniform float uIntensity;
void main() {
  float a = vAlpha * uIntensity;
  if (a < 0.01) discard;
  gl_FragColor = vec4(uColor, a);
}
`;

function buildSpeedLines(): { group: THREE.Group; meshes: THREE.Mesh[] } {
  const group = new THREE.Group();
  group.name = 'SpeedLines';
  const meshes: THREE.Mesh[] = [];

  const mat = new THREE.ShaderMaterial({
    vertexShader: SPEED_LINE_VERT,
    fragmentShader: SPEED_LINE_FRAG,
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
    uniforms: {
      uColor: { value: new THREE.Color(0.9, 0.95, 1.0) },
      uIntensity: { value: 0.0 },
    },
    // We'll inject per-vertex alpha via BufferAttribute.
  });

  // ~40 radial lines emanating from screen center.
  const count = 40;
  for (let i = 0; i < count; i++) {
    const geo = new THREE.BufferGeometry();
    // Each line is a long thin quad stretching from center outward.
    const angle = (i / count) * Math.PI * 2 + Math.random() * 0.3;
    const len = 8 + Math.random() * 16; // 8–24 m length
    const width = 0.02 + Math.random() * 0.05;
    const dist = 2 + Math.random() * 6;   // start distance from center

    // Quad vertices (center-outward line).
    const ax = Math.cos(angle);
    const az = Math.sin(angle);
    // Perpendicular for width.
    const px = -az;
    const pz = ax;

    const x0 = ax * dist + px * width * 0.5;
    const z0 = az * dist + pz * width * 0.5;
    const x1 = ax * dist - px * width * 0.5;
    const z1 = az * dist - pz * width * 0.5;
    const x2 = ax * (dist + len) - px * width * 0.3; // taper
    const z2 = az * (dist + len) - pz * width * 0.3;
    const x3 = ax * (dist + len) + px * width * 0.3;
    const z3 = az * (dist + len) + px * width * 0.3;

    const positions = new Float32Array([x0, 0, z0, x1, 0, z1, x2, 0, z2, x3, 0, z3]);
    const alphas = new Float32Array([0.0, 0.0, 1.0, 1.0]); // fade in toward tip
    const indices = [0, 1, 2, 1, 3, 2];

    geo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    geo.setAttribute('aAlpha', new THREE.Float32BufferAttribute(alphas, 1));
    geo.setIndex(indices);

    const mesh = new THREE.Mesh(geo, mat.clone()); // clone so we can fade individually
    mesh.frustumCulled = false;
    group.add(mesh);
    meshes.push(mesh);
  }

  return { group, meshes };
}

// ---- Vignette (darkens edges at high speed) --------------------------------

const VIGNETTE_VERT = `
varying vec2 vUv;
void main() {
  vUv = uv;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`;

const VIGNETTE_FRAG = `
varying vec2 vUv;
uniform float uDarkness;
uniform float uIntensity;
void main() {
  vec2 center = vUv - 0.5;
  float d = length(center);
  float vig = smoothstep(0.25, 0.85, d);
  float dark = mix(1.0, 1.0 - uDarkness, vig * uIntensity);
  gl_FragColor = vec4(0.0, 0.0, 0.0, 1.0 - dark);
}
`;

function buildVignette(): THREE.Mesh {
  const geo = new THREE.PlaneGeometry(2.2, 2.2);
  const mat = new THREE.ShaderMaterial({
    vertexShader: VIGNETTE_VERT,
    fragmentShader: VIGNETTE_FRAG,
    transparent: true,
    depthTest: false,
    depthWrite: false,
    uniforms: {
      uDarkness: { value: 0.7 },
      uIntensity: { value: 0.0 },
    },
  });
  const mesh = new THREE.Mesh(geo, mat);
  mesh.frustumCulled = false;
  mesh.renderOrder = 999; // draw last
  return mesh;
}

// ---- System ----------------------------------------------------------------

/**
 * SpeedEffectsSystem — post-process-style visual juice that runs in the `draw`
 * group. Adds speed lines (radial streaks), edge vignette darkening, and
 * intensity scaling based on vehicle speed fraction.
 *
 * This is NOT a real post-processing pass (no EffectComposer); it uses
 * overlay geometry parented to the camera so it works with the VibeGame
 * rendering pipeline as-is.
 */
export const SpeedEffectsSystem: System = defineSystem({
  name: 'SpeedEffectsSystem',
  group: 'draw',

  update(state: State) {
    if (state.headless) return;
    const scene = getScene(state);
    if (!scene) return;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const cam = (state as any).camera as THREE.Camera | undefined;

    const vehicles = vehicleQuery(state.world);

    for (const eid of vehicles) {
      let fx = effectState.get(eid);
      if (!fx) {
        const lines = buildSpeedLines();
        const vig = buildVignette();
        scene.add(lines.group);
        scene.add(vig);
        fx = {
          speedLines: lines.group,
          lineMeshes: lines.meshes,
          vignette: vig,
          chromaticMat: null,
          active: false,
        };
        effectState.set(eid, fx);
      }

      const speed = Vehicle.speed[eid] || 0;
      const maxSpeed = Vehicle.maxSpeed[eid] || 1;
      const speedFrac = Math.max(0, Math.min(1, Math.abs(speed) / maxSpeed));

      // Only show effects above ~35% speed.
      const threshold = 0.35;
      const intensity = speedFrac > threshold
        ? Math.pow((speedFrac - threshold) / (1 - threshold), 1.6)
        : 0;

      fx.active = intensity > 0.01;

      // Update speed lines.
      for (let i = 0; i < fx.lineMeshes.length; i++) {
        const mesh = fx.lineMeshes[i];
        const mat = mesh.material as THREE.ShaderMaterial & { uniforms: { uIntensity: { value: number } } };
        // Stagger each line's phase so they don't all pulse together.
        const phase = (performance.now() * 0.001 + i * 0.15) % 1;
        const pulse = 0.6 + 0.4 * Math.sin(phase * Math.PI * 2);
        mat.uniforms.uIntensity.value = fx.active ? intensity * pulse : 0;
        // Orient lines to face camera (billboard).
        mesh.quaternion.copy(cam?.quaternion ?? new THREE.Quaternion());
      }

      // Update vignette.
      const vigMat = fx.vignette.material as THREE.ShaderMaterial & {
        uniforms: { uIntensity: { value: number } };
      };
      vigMat.uniforms.uIntensity.value = intensity;

      // Parent effects to camera so they follow the view.
      if (cam) {
        fx.speedLines.position.copy(cam.position);
        fx.vignette.position.copy(cam.position);
        fx.vignette.quaternion.copy(cam.quaternion);
      }
    }
  },

  dispose() {
    for (const fx of effectState.values()) {
      fx.speedLines.parent?.remove(fx.speedLines);
      fx.vignette.parent?.remove(fx.vignette);
      // Dispose geometries/materials.
      for (const m of fx.lineMeshes) {
        m.geometry.dispose();
        (m.material as THREE.Material).dispose();
      }
      fx.vignette.geometry.dispose();
      (fx.vignette.material as THREE.Material).dispose();
    }
    effectState.clear();
  },
});
