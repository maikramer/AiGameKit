import * as THREE from 'three';
import { createNoise2D } from 'simplex-noise';
import { rngForSeed } from './rng';

export const RAIN_COUNT = 1600;
const BOX_XZ = 38;
const BOX_Y = 26;

/**
 * GPU rain: a static point cloud in a camera-following box; the vertex shader
 * wraps each drop's Y by time (zero per-frame CPU work beyond 4 uniform
 * writes). Drops render as short vertical streaks via gl_PointCoord alpha.
 *
 * When `seed` is provided (non-zero), placement is deterministic and uses
 * 2D simplex noise so drops cluster spatially instead of spreading uniformly.
 */
export function createRain(seed = 0): {
  points: THREE.Points;
  material: THREE.ShaderMaterial;
} {
  const rng = rngForSeed(seed);
  // Spatial noise gives rain a subtle coherent structure (denser/sparser
  // patches) when seeded; with the default (Math.random) it falls back to
  // uniform placement identical to the prior behaviour.
  const noise2d = createNoise2D(rng);
  const positions = new Float32Array(RAIN_COUNT * 3);
  const seeds = new Float32Array(RAIN_COUNT);
  for (let i = 0; i < RAIN_COUNT; i++) {
    if (seed) {
      // Map noise [-1,1] to placement ranges. Sample at varied frequencies so
      // X/Z/Y/speed aren't perfectly correlated.
      positions[i * 3] = noise2d(i * 0.07, 0.0) * BOX_XZ;
      positions[i * 3 + 1] = (noise2d(0.0, i * 0.11) * 0.5 + 0.5) * BOX_Y;
      positions[i * 3 + 2] = noise2d(i * 0.05, i * 0.13) * BOX_XZ;
      seeds[i] = 0.75 + (noise2d(i * 0.09, i * 0.17) * 0.5 + 0.5) * 0.5;
    } else {
      positions[i * 3] = (Math.random() - 0.5) * BOX_XZ * 2;
      positions[i * 3 + 1] = Math.random() * BOX_Y;
      positions[i * 3 + 2] = (Math.random() - 0.5) * BOX_XZ * 2;
      seeds[i] = 0.75 + Math.random() * 0.5; // per-drop fall-speed jitter
    }
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  geo.setAttribute('aSeed', new THREE.BufferAttribute(seeds, 1));
  geo.boundingSphere = new THREE.Sphere(
    new THREE.Vector3(0, BOX_Y / 2, 0),
    Math.hypot(BOX_XZ, BOX_Y)
  );

  const material = new THREE.ShaderMaterial({
    transparent: true,
    depthWrite: false,
    uniforms: {
      uTime: { value: 0 },
      uCenter: { value: new THREE.Vector3() },
      uWind: { value: new THREE.Vector2() },
      uOpacity: { value: 0 },
    },
    vertexShader: /* glsl */ `
      uniform float uTime;
      uniform vec3 uCenter;
      uniform vec2 uWind;
      attribute float aSeed;
      varying float vAlpha;
      const float BOX_Y = ${BOX_Y.toFixed(1)};
      void main() {
        float speed = 22.0 * aSeed;
        vec3 p = position;
        p.y = mod(p.y - uTime * speed, BOX_Y);
        // wind slant: drops shear sideways as they fall
        p.xz += uWind * (BOX_Y - p.y) * 0.04;
        vec3 world = p + uCenter - vec3(0.0, 2.0, 0.0);
        vec4 mv = modelViewMatrix * vec4(world, 1.0);
        gl_Position = projectionMatrix * mv;
        float dist = -mv.z;
        gl_PointSize = clamp(90.0 / max(dist, 1.0), 1.5, 7.0);
        vAlpha = 1.0 - smoothstep(20.0, 42.0, dist);
      }
    `,
    fragmentShader: /* glsl */ `
      uniform float uOpacity;
      varying float vAlpha;
      void main() {
        // vertical streak: narrow in X, soft falloff in Y
        vec2 c = gl_PointCoord - 0.5;
        float streak = (1.0 - smoothstep(0.06, 0.16, abs(c.x))) *
                       (1.0 - smoothstep(0.35, 0.5, abs(c.y)));
        float a = streak * vAlpha * uOpacity * 0.55;
        if (a < 0.01) discard;
        gl_FragColor = vec4(0.75, 0.82, 0.9, a);
      }
    `,
  });

  const points = new THREE.Points(geo, material);
  points.frustumCulled = false;
  points.renderOrder = 3;
  return { points, material };
}
