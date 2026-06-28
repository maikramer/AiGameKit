import {
  ACESFilmicToneMapping,
  AgXToneMapping,
  NeutralToneMapping,
  NoToneMapping,
  ReinhardToneMapping,
  Vector2,
  type Camera,
  type Scene,
  type ToneMapping,
  type WebGLRenderer,
} from 'three';
import { UnrealBloomPass } from 'three/examples/jsm/postprocessing/UnrealBloomPass.js';
import { ShaderPass } from 'three/examples/jsm/postprocessing/ShaderPass.js';
import { SMAAPass } from 'three/examples/jsm/postprocessing/SMAAPass.js';
import { SSAOPass } from 'three/examples/jsm/postprocessing/SSAOPass.js';
import { BokehPass } from 'three/examples/jsm/postprocessing/BokehPass.js';
import type { Pass } from 'three/examples/jsm/postprocessing/Pass.js';
import { FXAAShader } from 'three/examples/jsm/shaders/FXAAShader.js';
import { Postprocessing } from './components';
import { registerEffect } from './effect-registry';
import { HeightFogPass } from './height-fog';

type CS = Record<string, Float32Array | Uint8Array>;

// BokehPass aperture is a tiny sensitivity value (default 0.025) while the old
// postprocessing bokehScale is a coarse strength multiplier (~1-8). Scale so the
// default bokehScale lands near BokehPass's own default aperture.
const BOKEH_APERTURE_SCALE = 0.005;

// LINEAR was intentionally dropped from the tonemapping menu. Index 0 in the
// component selects NoToneMapping (a renderer side-effect, no Pass emitted).
const ToneMappings = [
  AgXToneMapping,
  ACESFilmicToneMapping,
  NeutralToneMapping,
  ReinhardToneMapping,
] as const;

registerEffect({
  key: 'smaa',
  position: 'first',
  create(
    _state: CS,
    entity: number,
    _renderer: WebGLRenderer,
    _scene: Scene,
    _camera: Camera
  ): Pass | null {
    const cs = Postprocessing as unknown as CS;
    if ((cs.aa as Uint8Array)[entity] !== 2) return null;
    return new SMAAPass();
  },
});

registerEffect({
  key: 'fxaa',
  position: 'first',
  create(
    _state: CS,
    entity: number,
    renderer: WebGLRenderer,
    _scene: Scene,
    _camera: Camera
  ): Pass | null {
    const cs = Postprocessing as unknown as CS;
    if ((cs.aa as Uint8Array)[entity] !== 1) return null;
    const size = renderer.getDrawingBufferSize(new Vector2());
    const pass = new ShaderPass(FXAAShader);
    (pass.uniforms.resolution.value as Vector2).set(1 / size.x, 1 / size.y);
    return pass;
  },
});

registerEffect({
  key: 'heightFog',
  create(
    _state: CS,
    entity: number,
    _renderer: WebGLRenderer,
    _scene: Scene,
    camera: Camera
  ): Pass | null {
    const cs = Postprocessing as unknown as CS;
    if (!(cs.heightFog as Uint8Array)[entity]) return null;
    return new HeightFogPass(camera, {
      color: (cs.fogColor as unknown as Uint32Array)[entity],
      density: (cs.fogDensity as Float32Array)[entity],
      height: (cs.fogHeight as Float32Array)[entity],
      falloff: (cs.fogFalloff as Float32Array)[entity],
      noise: (cs.fogNoise as Float32Array)[entity],
    });
  },
});

registerEffect({
  key: 'bloom',
  create(
    _state: CS,
    entity: number,
    renderer: WebGLRenderer,
    _scene: Scene,
    _camera: Camera
  ): Pass | null {
    const cs = Postprocessing as unknown as CS;
    if (!(cs.bloom as Uint8Array)[entity]) return null;
    const size = renderer.getDrawingBufferSize(new Vector2());
    return new UnrealBloomPass(
      size,
      (cs.bloomStrength as Float32Array)[entity],
      (cs.bloomRadius as Float32Array)[entity],
      (cs.bloomThreshold as Float32Array)[entity]
    );
  },
  update(state: CS, entity: number, pass: Pass): void {
    const bloom = pass as UnrealBloomPass;
    bloom.strength = (state.bloomStrength as Float32Array)[entity];
    bloom.radius = (state.bloomRadius as Float32Array)[entity];
    bloom.threshold = (state.bloomThreshold as Float32Array)[entity];
  },
});

registerEffect({
  key: 'vignette',
  create(
    _state: CS,
    entity: number,
    _renderer: WebGLRenderer,
    _scene: Scene,
    _camera: Camera
  ): Pass | null {
    const cs = Postprocessing as unknown as CS;
    if (!(cs.vignette as Uint8Array)[entity]) return null;
    const pass = new ShaderPass({
      uniforms: {
        tDiffuse: { value: null },
        offset: { value: (cs.vignetteOffset as Float32Array)[entity] },
        darkness: { value: (cs.vignetteDarkness as Float32Array)[entity] },
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
        uniform float offset;
        uniform float darkness;
        varying vec2 vUv;
        void main() {
          vec4 texel = texture2D(tDiffuse, vUv);
          vec2 uv = (vUv - 0.5) * (1.0 + offset);
          float vig = clamp(1.0 - dot(uv, uv) * darkness, 0.0, 1.0);
          gl_FragColor = vec4(texel.rgb * vig, texel.a);
        }
      `,
    });
    return pass;
  },
  update(state: CS, entity: number, pass: Pass): void {
    const v = pass as ShaderPass;
    v.uniforms.offset.value = (state.vignetteOffset as Float32Array)[entity];
    v.uniforms.darkness.value = (state.vignetteDarkness as Float32Array)[
      entity
    ];
  },
});

registerEffect({
  key: 'ssao',
  create(
    _state: CS,
    entity: number,
    renderer: WebGLRenderer,
    scene: Scene,
    camera: Camera
  ): Pass | null {
    const cs = Postprocessing as unknown as CS;
    if (!(cs.ssao as Uint8Array)[entity]) return null;
    const size = renderer.getDrawingBufferSize(new Vector2());
    const pass = new SSAOPass(scene, camera, size.x, size.y);
    pass.kernelRadius = Math.max(1e-6, (cs.ssaoRadius as Float32Array)[entity]);
    // SSAOPass exposes no `.intensity` property: AO strength is baked into the
    // output blend, and spread is governed by kernelRadius/minDistance/maxDistance.
    // ssaoIntensity therefore has no 1:1 mapping here; tuning required to recover
    // the previous visual.
    return pass;
  },
  update(state: CS, entity: number, pass: Pass): void {
    const ssao = pass as SSAOPass;
    ssao.kernelRadius = Math.max(
      1e-6,
      (state.ssaoRadius as Float32Array)[entity]
    );
    // ssaoIntensity has no SSAOPass equivalent (see create() TODO).
  },
});

registerEffect({
  key: 'depthOfField',
  create(
    _state: CS,
    entity: number,
    _renderer: WebGLRenderer,
    scene: Scene,
    camera: Camera
  ): Pass | null {
    const cs = Postprocessing as unknown as CS;
    if (!(cs.depthOfField as Uint8Array)[entity]) return null;
    // BokehPass parameterization differs from the previous DepthOfFieldEffect:
    //   focus     = focusDistance (world units along the view ray)
    //   aperture  = bokehScale * BOKEH_APERTURE_SCALE (mapped to BokehPass's
    //               tiny aperture sensitivity, default 0.025)
    //   maxblur   = clamp(focusRange, 0, 1) (reused as the max-blur cap)
    const focus = (cs.dofFocusDistance as Float32Array)[entity];
    const aperture =
      (cs.dofBokehScale as Float32Array)[entity] * BOKEH_APERTURE_SCALE;
    const maxblur = Math.min(1, (cs.dofFocusRange as Float32Array)[entity]);
    return new BokehPass(scene, camera, { focus, aperture, maxblur });
  },
  update(state: CS, entity: number, pass: Pass): void {
    const dof = pass as BokehPass;
    dof.materialBokeh.uniforms.focus.value = (
      state.dofFocusDistance as Float32Array
    )[entity];
    dof.materialBokeh.uniforms.aperture.value =
      (state.dofBokehScale as Float32Array)[entity] * BOKEH_APERTURE_SCALE;
    dof.materialBokeh.uniforms.maxblur.value = Math.min(
      1,
      (state.dofFocusRange as Float32Array)[entity]
    );
  },
});

registerEffect({
  key: 'tonemapping',
  position: 'last',
  create(
    _state: CS,
    entity: number,
    renderer: WebGLRenderer,
    _scene: Scene,
    _camera: Camera
  ): Pass | null {
    const cs = Postprocessing as unknown as CS;
    const idx = (cs.toneMapping as Uint8Array)[entity];
    if (idx === 0) {
      renderer.toneMapping = NoToneMapping;
      return null;
    }
    renderer.toneMapping = ToneMappings[
      Math.min(idx, ToneMappings.length) - 1
    ] as ToneMapping;
    renderer.toneMappingExposure = (cs.toneMappingExposure as Float32Array)[
      entity
    ];
    return null;
  },
});

registerEffect({
  key: 'chromaticAberration',
  create(
    _state: CS,
    entity: number,
    _renderer: WebGLRenderer,
    _scene: Scene,
    _camera: Camera
  ): Pass | null {
    const cs = Postprocessing as unknown as CS;
    if (!(cs.chromaticAberration as Uint8Array)[entity]) return null;
    const strength = (cs.caStrength as Float32Array)[entity];
    const pass = new ShaderPass({
      uniforms: {
        tDiffuse: { value: null },
        caOffset: { value: new Vector2(strength, strength) },
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
        uniform vec2 caOffset;
        varying vec2 vUv;
        void main() {
          vec2 offset = caOffset * (vUv - 0.5);
          float r = texture2D(tDiffuse, vUv - offset).r;
          float g = texture2D(tDiffuse, vUv).g;
          float b = texture2D(tDiffuse, vUv + offset).b;
          gl_FragColor = vec4(r, g, b, 1.0);
        }
      `,
    });
    return pass;
  },
  update(state: CS, entity: number, pass: Pass): void {
    const ca = pass as ShaderPass;
    const strength = (state.caStrength as Float32Array)[entity];
    (ca.uniforms.caOffset.value as Vector2).set(strength, strength);
  },
});

export function registerBuiltinEffects(): void {}
