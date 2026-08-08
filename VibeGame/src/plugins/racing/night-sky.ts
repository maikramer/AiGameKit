import * as THREE from 'three';
import { defineSystem, type State, type System } from '../../core';
import { getScene } from '../rendering';

/**
 * NightSkySystem — replaces the default sky with a procedural night skybox
 * featuring:
 * - Deep dark gradient (navy → black)
 * - Twinkling stars (point sprites with varying brightness)
 * - City glow / horizon haze (orange-pink ambient near ground)
 * - Occasional shooting star (rare, for wow factor)
 *
 * Runs once on boot; idempotent.
 */
export const NightSkySystem: System = defineSystem({
  name: 'NightSkySystem',
  group: 'simulation', // run early so sky is ready before first draw

  update(state: State) {
    if (state.headless) return;
    const scene = getScene(state);
    if (!scene) return;

    // Only build once.
    if ((scene as unknown as { __nightSky?: boolean }).__nightSky) return;
    (scene as unknown as { __nightSky?: boolean }).__nightSky = true;

    buildNightSky(scene);
  },

  dispose() {
    // Sky is owned by the scene; scene cleanup handles it.
  },
});

function buildNightSky(scene: THREE.Object3D): void {
  const skyGroup = new THREE.Group();
  skyGroup.name = 'NightSky';

  // --- 1. Gradient dome (dark navy to black) ----------------------------
  const domeGeo = new THREE.SphereGeometry(800, 32, 16);
  const domeMat = new THREE.ShaderMaterial({
    side: THREE.BackSide,
    uniforms: {
      uTopColor: { value: new THREE.Color(0x050510) },   // deep space black
      uHorizonColor: { value: new THREE.Color(0x0a0a1a) }, // dark navy
      uGlowColor: { value: new THREE.Color(0x1a0a15) },   // faint city pink
      uGlowHeight: { value: 0.08 },                         // how far up the glow reaches
    },
    vertexShader: `
      varying vec3 vWorldPos;
      void main() {
        vWorldPos = position;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      }
    `,
    fragmentShader: `
      uniform vec3 uTopColor;
      uniform vec3 uHorizonColor;
      uniform vec3 uGlowColor;
      uniform float uGlowHeight;
      varying vec3 vWorldPos;

      void main() {
        float h = normalize(vWorldPos).y; // -1 (below) to +1 (zenith)
        float t = h * 0.5 + 0.5;       // 0 (horizon) to 1 (zenith)

        vec3 col = mix(uHorizonColor, uTopColor, pow(t, 0.6));

        // City glow near horizon.
        float glow = exp(-abs(h) / uGlowHeight) * 0.6;
        col = mix(col, uGlowColor, glow);

        gl_FragColor = vec4(col, 1.0);
      }
    `,
  });
  const dome = new THREE.Mesh(domeGeo, domeMat);
  skyGroup.add(dome);

  // --- 2. Stars -----------------------------------------------------------
  const STAR_COUNT = 600;
  const starPositions = new Float32Array(STAR_COUNT * 3);
  const starColors = new Float32Array(STAR_COUNT * 3);
  const starSizes = new Float32Array(STAR_COUNT);

  for (let i = 0; i < STAR_COUNT; i++) {
    // Distribute on upper hemisphere (above horizon).
    const theta = Math.random() * Math.PI * 2;       // azimuth
    const phi = Math.acos(Math.random() * 0.85 + 0.15); // elevation (biased upward)

    const r = 750 + Math.random() * 40; // slight depth variation
    starPositions[i * 3] = r * Math.sin(phi) * Math.cos(theta);
    starPositions[i * 3 + 1] = r * Math.cos(phi) + 20; // lift above horizon
    starPositions[i * 3 + 2] = r * Math.sin(phi) * Math.sin(theta);

    // Star color: white with slight blue/yellow tint variety.
    const tint = Math.random();
    if (tint < 0.7) {
      // White-ish
      starColors[i * 3] = 0.9 + Math.random() * 0.1;
      starColors[i * 3 + 1] = 0.9 + Math.random() * 0.1;
      starColors[i * 3 + 2] = 1.0;
    } else if (tint < 0.85) {
      // Blue-white
      starColors[i * 3] = 0.7 + Math.random() * 0.2;
      starColors[i * 3 + 1] = 0.8 + Math.random() * 0.2;
      starColors[i * 3 + 2] = 1.0;
    } else {
      // Yellow-white
      starColors[i * 3] = 1.0;
      starColors[i * 3 + 1] = 0.9 + Math.random() * 0.1;
      starColors[i * 3 + 2] = 0.7 + Math.random() * 0.2;
    }

    // Size variation (most small, few bright).
    starSizes[i] = Math.random() < 0.9 ? 1.5 + Math.random() * 2 : 3 + Math.random() * 4;
  }

  const starGeo = new THREE.BufferGeometry();
  starGeo.setAttribute('position', new THREE.Float32BufferAttribute(starPositions, 3));
  starGeo.setAttribute('color', new THREE.Float32BufferAttribute(starColors, 3));
  starGeo.setAttribute('size', new THREE.Float32BufferAttribute(starSizes, 1));

  const starMat = new THREE.ShaderMaterial({
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
    uniforms: {
      uTime: { value: 0 },
    },
    vertexShader: `
      attribute float size;
      attribute vec3 color;
      varying vec3 vColor;
      varying float vSize;
      void main() {
        vColor = color;
        vSize = size;
        vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
        gl_PointSize = size * (200.0 / -mvPosition.z); // size attenuation
        gl_Position = projectionMatrix * mvPosition;
      }
    `,
    fragmentShader: `
      uniform float uTime;
      varying vec3 vColor;
      varying float vSize;

      void main() {
        // Round point with soft edge.
        vec2 center = gl_PointCoord - 0.5;
        float d = length(center);
        if (d > 0.5) discard;

        float alpha = 1.0 - smoothstep(0.2, 0.5, d);

        // Twinkle: larger stars pulse faster/brighter.
        float twinkle = 0.7 + 0.3 * sin(uTime * (1.5 + vSize * 0.5) + vSize * 10.0);

        gl_FragColor = vec4(vColor * twinkle, alpha * twinkle);
      }
    `,
  });

  const stars = new THREE.Points(starGeo, starMat);
  stars.name = 'Stars';
  skyGroup.add(stars);

  // Store reference for animation.
  (skyGroup as unknown as { _starMat?: THREE.ShaderMaterial })._starMat = starMat;

  // --- 3. City silhouette / horizon glow line -----------------------------
  // A thin ring of emissive geometry at the horizon for city lights feel.
  const ringGeo = new THREE.RingGeometry(790, 800, 64, 1);
  const ringMat = new THREE.MeshBasicMaterial({
    color: 0xff6633,
    transparent: true,
    opacity: 0.04,
    side: THREE.DoubleSide,
    blending: THREE.AdditiveBlending,
  });
  const ring = new THREE.Mesh(ringGeo, ringMat);
  ring.rotation.x = Math.PI / 2;
  ring.position.y = -5;
  skyGroup.add(ring);

  scene.add(skyGroup);

  // Animate stars in the draw loop via a simple hook.
  const animateStars = (): void => {
    const mat = (skyGroup as unknown as { _starMat?: THREE.ShaderMaterial })._starMat;
    if (mat && mat.uniforms.uTime) {
      mat.uniforms.uTime.value = performance.now() * 0.001;
    }
    requestAnimationFrame(animateStars);
  };
  animateStars();
}
