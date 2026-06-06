import * as THREE from "three";
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js";
import { RenderPass } from "three/examples/jsm/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/examples/jsm/postprocessing/UnrealBloomPass.js";
import { ShaderPass } from "three/examples/jsm/postprocessing/ShaderPass.js";
import { SMAAPass } from "three/examples/jsm/postprocessing/SMAAPass.js";
import { FXAAShader } from "three/examples/jsm/shaders/FXAAShader.js";
import { GammaCorrectionShader } from "three/examples/jsm/shaders/GammaCorrectionShader.js";

export const AAMode = { OFF: 0, FXAA: 1, SMAA: 2 } as const;

export interface PostFxConfig {
  enabled: number;
  bloom: boolean;
  bloomStrength: number;
  bloomRadius: number;
  bloomThreshold: number;
  chromaticAberration: boolean;
  chromaticAberrationStrength: number;
  vignette: boolean;
  vignetteStrength: number;
  vignetteSmoothness: number;
  fxaa: boolean;
  smaa: boolean;
  smaaQuality: number;
  tonemapping: number;
  dither: number;
  aa: number;
}

export type PostProcessingPipeline = EffectComposer;

const ChromaticAberrationShader = {
  uniforms: {
    tDiffuse: { value: null },
    uStrength: { value: 0.003 },
    uDirection: { value: new THREE.Vector2(1.0, 1.0) },
  },
  vertexShader: /* glsl */ `
    varying vec2 vUv;
    void main() {
      vUv = uv;
      gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
    }
  `,
  fragmentShader: /* glsl */ `
    uniform sampler2D tDiffuse;
    uniform float uStrength;
    uniform vec2 uDirection;
    varying vec2 vUv;
    void main() {
      vec2 dir = (vUv - 0.5) * uDirection;
      float dist = length(dir);
      float strength = uStrength * dist;
      vec4 cr = texture2D(tDiffuse, vUv + dir * strength);
      vec4 cg = texture2D(tDiffuse, vUv);
      vec4 cb = texture2D(tDiffuse, vUv - dir * strength);
      gl_FragColor = vec4(cr.r, cg.g, cb.b, cg.a);
    }
  `,
};

const VignetteShader = {
  uniforms: {
    tDiffuse: { value: null },
    uStrength: { value: 0.5 },
    uSmoothness: { value: 0.85 },
  },
  vertexShader: /* glsl */ `
    varying vec2 vUv;
    void main() {
      vUv = uv;
      gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
    }
  `,
  fragmentShader: /* glsl */ `
    uniform sampler2D tDiffuse;
    uniform float uStrength;
    uniform float uSmoothness;
    varying vec2 vUv;
    void main() {
      vec4 color = texture2D(tDiffuse, vUv);
      float dist = distance(vUv, vec2(0.5));
      float vignette = smoothstep(uSmoothness, uSmoothness - 0.4, dist * uStrength * 2.0);
      gl_FragColor = vec4(color.rgb * mix(1.0, vignette, uStrength), color.a);
    }
  `,
};

export function buildPostProcessing(
  renderer: THREE.WebGLRenderer,
  scene: THREE.Scene,
  camera: THREE.Camera,
  config: PostFxConfig,
): EffectComposer | null {
  if (!config.enabled) return null;

  const size = renderer.getSize(new THREE.Vector2());
  const composer = new EffectComposer(renderer);

  composer.addPass(new RenderPass(scene, camera));

  if (config.bloom) {
    const bloomPass = new UnrealBloomPass(
      new THREE.Vector2(size.x, size.y),
      config.bloomStrength,
      config.bloomRadius,
      config.bloomThreshold,
    );
    composer.addPass(bloomPass);
  }

  if (config.chromaticAberration) {
    const caPass = new ShaderPass(ChromaticAberrationShader);
    caPass.uniforms["uStrength"].value = config.chromaticAberrationStrength;
    composer.addPass(caPass);
  }

  if (config.vignette) {
    const vignettePass = new ShaderPass(VignetteShader);
    vignettePass.uniforms["uStrength"].value = config.vignetteStrength;
    vignettePass.uniforms["uSmoothness"].value = config.vignetteSmoothness;
    composer.addPass(vignettePass);
  }

  if (config.aa === AAMode.FXAA) {
    const fxaaPass = new ShaderPass(FXAAShader);
    fxaaPass.uniforms["resolution"].value.set(1 / size.x, 1 / size.y);
    composer.addPass(fxaaPass);
  } else if (config.aa === AAMode.SMAA) {
    composer.addPass(new SMAAPass());
  }

  const gammaPass = new ShaderPass(GammaCorrectionShader);
  composer.addPass(gammaPass);

  return composer;
}
