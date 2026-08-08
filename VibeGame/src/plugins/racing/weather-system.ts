import * as THREE from 'three';
import { defineSystem, type State, type System } from '../../core';
import { getScene } from '../rendering';

/**
 * WeatherSystem — adds rain particles and ground puddles that reflect
 * neon street lights. Purely visual (no physics impact on car).
 *
 * Rain: falling particle streaks (fast, dense at high intensity).
 * Puddles: reflective planes near the road surface that mirror lights.
 */
export const WeatherSystem: System = defineSystem({
  name: 'WeatherSystem',
  group: 'draw',

  update(state: State) {
    if (state.headless) return;
    const scene = getScene(state);
    if (!scene) return;

    // Lazy init.
    if (!(scene as unknown as { __weatherInit?: boolean }).__weatherInit) {
      (scene as unknown as { __weatherInit?: boolean }).__weatherInit = true;
      try {
        buildWeather(scene);
      } catch (e) {
        console.warn('[Racing] Weather system not available:', e);
      }
    }
  },

  dispose() {},
});

function buildWeather(scene: THREE.Object3D): void {
  const weatherGroup = new THREE.Group();
  weatherGroup.name = 'Weather';

  // --- Rain particles ----------------------------------------------------
  const RAIN_COUNT = 800;
  const rainPositions = new Float32Array(RAIN_COUNT * 3);
  const rainVelocities = new Float32Array(RAIN_COUNT); // fall speed variation

  for (let i = 0; i < RAIN_COUNT; i++) {
    // Spread over a large area above the camera.
    rainPositions[i * 3] = (Math.random() - 0.5) * 120; // X
    rainPositions[i * 3 + 1] = Math.random() * 40 + 10;      // Y (above)
    rainPositions[i * 3 + 2] = (Math.random() - 0.5) * 120; // Z
    rainVelocities[i] = 15 + Math.random() * 20; // 15-35 m/s fall speed
  }

  const rainGeo = new THREE.BufferGeometry();
  rainGeo.setAttribute('position', new THREE.Float32BufferAttribute(rainPositions, 3));

  const rainMat = new THREE.ShaderMaterial({
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
    uniforms: {
      uTime: { value: 0 },
    },
    vertexShader: `
      attribute float velocity;
      varying float vAlpha;
      // GLSL has no random() — provide a cheap hash so each drop has its own
      // alpha (position jitter via the hash input).
      float hash13(vec3 p) {
        p = fract(p * 0.1031);
        p += dot(p, p.zyx + 31.32);
        return fract((p.x + p.y) * p.z);
      }
      void main() {
        vAlpha = 0.4 + 0.6 * hash13(position);
        vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
        gl_PointSize = 1.2;
        gl_Position = projectionMatrix * mvPosition;
      }
    `,
    fragmentShader: `
      varying float vAlpha;
      void main() {
        float d = length(gl_PointCoord - 0.5);
        if (d > 0.5) discard;
        // Streak shape (elongated vertically).
        float streak = smoothstep(0.3, 0.5, d) * smoothstep(0.0, 0.3, d);
        gl_FragColor = vec4(0.6, 0.7, 0.9, vAlpha * (1.0 - streak) * 0.35);
      }
    `,
  });

  const rain = new THREE.Points(rainGeo, rainMat);
  rain.name = 'Rain';
  rain.frustumCulled = false;
  weatherGroup.add(rain);

  // Animate rain falling.
  const animateRain = (): void => {
    const pos = rain.geometry.attributes.position as THREE.BufferAttribute;
    const arr = pos.array as Float32Array;
    for (let i = 0; i < RAIN_COUNT; i++) {
      arr[i * 3 + 1] -= rainVelocities[i] * 0.016; // ~60fps timestep
      // Reset when below ground.
      if (arr[i * 3 + 1] < -2) {
        arr[i * 3 + 1] = 30 + Math.random() * 15; // respawn high
        arr[i * 3] = (Math.random() - 0.5) * 120; // random X/Z
      }
    }
    pos.needsUpdate = true;
    requestAnimationFrame(animateRain);
  };
  animateRain();

  // --- Ground puddles (reflective planes) ---------------------------------
  const puddleCount = 12;
  for (let p = 0; p < puddleCount; p++) {
    const size = 2 + Math.random() * 6;
    const puddleGeo = new THREE.CircleGeometry(size, 24); // segmented circle for better reflection
    puddleGeo.rotateX(-Math.PI / 2);

    // Reflective material that picks up nearby lights.
    const puddleMat = new THREE.MeshStandardMaterial({
      color: 0x111118,
      roughness: 0.05,   // very glossy
      metalness: 0.9,     // mirror-like
      transparent: true,
      opacity: 0.35,
      envMapIntensity: 2.0,
    });

    const puddle = new THREE.Mesh(puddleGeo, puddleMat);
    puddle.position.set(
      (Math.random() - 0.5) * 80,
      0.02, // just above ground
      (Math.random() - 0.5) * 80
    );
    puddle.rotation.y = Math.random() * Math.PI;
    puddle.name = `Puddle_${p}`;
    puddle.receiveShadow = false;
    weatherGroup.add(puddle);
  }

  // --- Mist/fog volume near ground --------------------------------------
  const mistGeo = new THREE.BoxGeometry(200, 8, 200);
  const mistMat = new THREE.MeshBasicMaterial({
    color: 0x334466,
    transparent: true,
    opacity: 0.06,
    depthWrite: false,
    blending: THREE.NormalBlending,
  });
  const mist = new THREE.Mesh(mistGeo, mistMat);
  mist.position.y = 3; // floating just above ground
  mist.name = 'GroundMist';
  weatherGroup.add(mist);

  scene.add(weatherGroup);
}
