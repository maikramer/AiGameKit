import * as THREE from 'three';
import { defineSystem, defineQuery, type State, type System } from '../../core';
import { getScene } from '../rendering';
import { Vehicle } from './components';

const vehicleQuery = defineQuery([Vehicle]);

interface PostProcessingState {
  composer: any; // EffectComposer (from three/addons)
  bloomPass: any; // UnrealBloomPass
  chromaticAberration: any; // ShaderPass
  vignettePass: any; // ShaderPass
  initialized: boolean;
}

const ppState: PostProcessingState = {
  composer: null,
  bloomPass: null,
  chromaticAberration: null,
  vignettePass: null,
  initialized: false,
};

// ---- Custom Shaders -------------------------------------------------------

const CHROMATIC_ABERRATION_SHADER = {
  uniforms: {
    tDiffuse: { value: null },
    uIntensity: { value: 0.002 },
    uDirection: { value: new THREE.Vector2(1, 0) },
  },
  vertexShader: `
    varying vec2 vUv;
    void main() {
      vUv = uv;
      gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
    }
  `,
  fragmentShader: `
    uniform sampler2D tDiffuse;
    uniform float uIntensity;
    uniform vec2 uDirection;
    varying vec2 vUv;

    void main() {
      // Sample R and B channels slightly offset for chromatic aberration.
      float offset = uIntensity;
      vec2 dir = normalize(uDirection);
      vec4 cr = texture2D(tDiffuse, vUv + dir * offset);
      vec4 cg = texture2D(tDiffuse, vUv);
      vec4 cb = texture2D(tDiffuse, vUv - dir * offset);
      gl_FragColor = vec4(cr.r, cg.g, cb.b, cg.a);
    }
  `,
};

const DYNAMIC_VIGNETTE_SHADER = {
  uniforms: {
    tDiffuse: { value: null },
    uDarkness: { value: 0.5 },
    uIntensity: { value: 0.0 },
  },
  vertexShader: `
    varying vec2 vUv;
    void main() {
      vUv = uv;
      gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
    }
  `,
  fragmentShader: `
    uniform sampler2D tDiffuse;
    uniform float uDarkness;
    uniform float uIntensity;
    varying vec2 vUv;

    void main() {
      vec4 color = texture2D(tDiffuse, vUv);
      vec2 center = vUv - 0.5;
      float d = length(center);
      float vig = smoothstep(0.2, 0.9, d);
      float dark = mix(1.0, 1.0 - uDarkness, vig * uIntensity);
      gl_FragColor = color * dark;
    }
  `,
};

/**
 * PostProcessingSystem — adds real post-processing effects via EffectComposer.
 *
 * Effects (all configurable per-vehicle speed):
 * - **Bloom**: Neon glow on emissive materials (kerbs, edge glow, taillights)
 * - **Chromatic Aberration**: RGB split at edges, increases with speed
 * - **Dynamic Vignette**: Darkens edges at high speed for focus/tunnel effect
 *
 * Uses `three/examples/jsm/postprocessing/` modules. Falls back gracefully if
 * imports fail (headless, missing module).
 */
export const PostProcessingSystem: System = defineSystem({
  name: 'PostProcessingSystem',
  group: 'draw', // after rendering, before present

  update(state: State) {
    if (state.headless) return;

    // Graceful fallback: skip entirely if we can't get basics.
    const scene = getScene(state);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const camera = (state as any).camera as THREE.Camera | undefined;
    if (!scene || !camera) return;

    // Lazy init — needs renderer + scene + camera.
    if (!ppState.initialized) {
      try {
        initializePostProcessing(null, scene, state);
        ppState.initialized = true;
      } catch (e) {
        console.warn('[Racing] Post-processing not available:', e);
        ppState.initialized = true; // don't retry every frame
        return;
      }
    }

    if (!ppState.composer) return;

    // Read speed from player vehicle to drive effect intensity.
    let maxSpeedFrac = 0;
    const vehicles = vehicleQuery(state.world);
    for (const eid of vehicles) {
      const speed = Vehicle.speed[eid] || 0;
      const maxSpeed = Vehicle.maxSpeed[eid] || 1;
      const frac = Math.abs(speed) / maxSpeed;
      if (frac > maxSpeedFrac) maxSpeedFrac = frac;
    }

    // --- Bloom: stronger at night (always on for neon aesthetic) ----------
    if (ppState.bloomPass) {
      // Base bloom for neon + speed boost.
      const baseStrength = 0.35;
      const speedBoost = maxSpeedFrac * 0.25;
      ppState.bloomPass.strength = baseStrength + speedBoost;
      ppState.bloomPass.radius = 0.6 + maxSpeedFrac * 0.3;
      ppState.bloomPass.threshold = 0.7;
    }

    // --- Chromatic Aberration: increases with speed ----------------------
    if (ppState.chromaticAberration) {
      const mat = ppState.chromaticAberration.material as THREE.ShaderMaterial & {
        uniforms: { uIntensity: { value: number } };
      };
      // 0.001 at rest → 0.006 at top speed.
      mat.uniforms.uIntensity.value = 0.001 + maxSpeedFrac * 0.005;
    }

    // --- Dynamic Vignette: tunnel vision at high speed --------------------
    if (ppState.vignettePass) {
      const mat = ppState.vignettePass.material as THREE.ShaderMaterial & {
        uniforms: { uIntensity: { value: number } };
      };
      mat.uniforms.uIntensity.value = Math.pow(maxSpeedFrac, 1.5) * 0.8;
    }

    // Render via composer instead of default renderer.
    // The composer handles renderTarget swap automatically.
    const size = { width: window.innerWidth, height: window.innerHeight };
    if (
      ppState.composer &&
      (ppState.composer as any).setSize &&
      size.width > 0 &&
      size.height > 0
    ) {
      // Check if resize needed.
      const currentW = (ppState.composer as any).renderer?.size?.x ?? 0;
      if (Math.abs(currentW - size.width) > 1) {
        (ppState.composer as any).setSize(size.width, size.height);
      }

      ppState.composer.render();
    }
  },

  dispose() {
    if (ppState.composer) {
      ppState.composer.dispose();
      ppState.composer = null;
    }
    ppState.bloomPass = null;
    ppState.chromaticAberration = null;
    ppState.vignettePass = null;
    ppState.initialized = false;
  },
});

async function initializePostProcessing(
  _renderer: THREE.WebGLRenderer | null,
  scene: THREE.Object3D,
  state: State
): Promise<void> {
  // Try to get renderer from VibeGame state or global.
  let renderer: THREE.WebGLRenderer | null = null;

  // Method 1: try VibeGame internal rendering context.
  try {
    const { getRenderingContext } = await import('../rendering/utils.js');
    const ctx = getRenderingContext(state as State);
    renderer = ctx.renderer || null;
  } catch {
    // ignore - will try other methods
  }

  // Method 2: try to find renderer via the canvas element.
  if (!renderer) {
    const canvas = document.querySelector('#game-canvas') as HTMLCanvasElement | null;
    if (canvas) {
      const context = canvas.getContext('webgl2') || canvas.getContext('webgl');
      if (context) {
        // Create a minimal renderer wrapper info.
        console.warn('[Racing] Using fallback renderer detection for post-processing');
      }
    }
  }

  // Dynamic import of three/addons postprocessing.
  let EffectComposer: any, RenderPass: any, ShaderPass: any, UnrealBloomPass: any;
  try {
    const modComposer = await import('three/examples/jsm/postprocessing/EffectComposer.js');
    EffectComposer = modComposer.EffectComposer;
    const modRender = await import('three/examples/jsm/postprocessing/RenderPass.js');
    RenderPass = modRender.RenderPass;
    const modShader = await import('three/examples/jsm/postprocessing/ShaderPass.js');
    ShaderPass = modShader.ShaderPass;
    const modBloom = await import('three/examples/jsm/postprocessing/UnrealBloomPass.js');
    UnrealBloomPass = modBloom.UnrealBloomPass;
  } catch (e) {
    console.warn('[Racing] Post-processing modules not available:', e);
    ppState.initialized = true; // mark done so we don't retry
    return;
  }

  // Use window size as fallback.
  const w = window.innerWidth;
  const h = window.innerHeight;

  // If we have a real renderer, use it. Otherwise create a dummy composer that won't render.
  let composer: any;
  if (renderer) {
    composer = new EffectComposer(renderer);
    composer.setSize(w, h);

    // 1. Render pass (renders the scene).
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const cam = (state as any).camera as THREE.Camera | undefined;
    const renderPass = new RenderPass(scene as THREE.Scene, cam || new THREE.Camera());
    composer.addPass(renderPass);

    // 2. Bloom pass (neon glow).
    if (UnrealBloomPass) {
      const bloom = new UnrealBloomPass(new THREE.Vector2(w, h), 0.35, 0.6, 0.7);
      composer.addPass(bloom);
      ppState.bloomPass = bloom;
    }

    // 3. Chromatic aberration (speed-based RGB split).
    const chromaticShader = { ...CHROMATIC_ABERRATION_SHADER };
    const chromaticPass = new ShaderPass(chromaticShader);
    chromaticPass.renderToScreen = false;
    composer.addPass(chromaticPass);
    ppState.chromaticAberration = chromaticPass;

    // 4. Dynamic vignette (tunnel vision).
    const vigShader = { ...DYNAMIC_VIGNETTE_SHADER };
    const vigPass = new ShaderPass(vigShader);
    vigPass.renderToScreen = true; // final pass
    composer.addPass(vigPass);
    ppState.vignettePass = vigPass;

    ppState.composer = composer;
  } else {
    // No renderer available — disable gracefully.
    console.warn('[Racing] Post-processing disabled (no renderer)');
    ppState.composer = null;
  }
}
