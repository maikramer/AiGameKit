import { Vector2 } from 'three';
import {
  BlendFunction,
  BloomEffect,
  ChromaticAberrationEffect,
  DepthOfFieldEffect,
  FXAAEffect,
  SMAAEffect,
  SMAAPreset,
  SSAOEffect,
  ToneMappingEffect,
  ToneMappingMode,
  VignetteEffect,
} from 'postprocessing';
import type { Camera, Scene, WebGLRenderer } from 'three';
import type { Effect } from 'postprocessing';
import { Postprocessing } from './components';
import { registerEffect } from './effect-registry';
import { HeightFogEffect } from './height-fog';

type CS = Record<string, Float32Array | Uint8Array>;

const ToneMappingModes = [
  ToneMappingMode.LINEAR,
  ToneMappingMode.AGX,
  ToneMappingMode.ACES_FILMIC,
  ToneMappingMode.NEUTRAL,
  ToneMappingMode.REINHARD,
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
  ): Effect | null {
    const cs = Postprocessing as unknown as CS;
    if ((cs.aa as Uint8Array)[entity] !== 2) return null;
    return new SMAAEffect({ preset: SMAAPreset.HIGH });
  },
});

registerEffect({
  key: 'fxaa',
  position: 'first',
  create(
    _state: CS,
    entity: number,
    _renderer: WebGLRenderer,
    _scene: Scene,
    _camera: Camera
  ): Effect | null {
    const cs = Postprocessing as unknown as CS;
    if ((cs.aa as Uint8Array)[entity] !== 1) return null;
    return new FXAAEffect();
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
  ): Effect | null {
    const cs = Postprocessing as unknown as CS;
    if (!(cs.heightFog as Uint8Array)[entity]) return null;
    return new HeightFogEffect(camera, {
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
    _renderer: WebGLRenderer,
    _scene: Scene,
    _camera: Camera
  ): Effect | null {
    const cs = Postprocessing as unknown as CS;
    if (!(cs.bloom as Uint8Array)[entity]) return null;
    return new BloomEffect({
      blendFunction: BlendFunction.SCREEN,
      luminanceThreshold: (cs.bloomThreshold as Float32Array)[entity],
      intensity: (cs.bloomStrength as Float32Array)[entity],
      radius: (cs.bloomRadius as Float32Array)[entity],
      mipmapBlur: true,
    });
  },
  update(state: CS, entity: number, effect: Effect): void {
    const bloom = effect as BloomEffect;
    bloom.intensity = (state.bloomStrength as Float32Array)[entity];
    bloom.mipmapBlurPass.radius = (state.bloomRadius as Float32Array)[entity];
    bloom.luminanceMaterial.threshold = (state.bloomThreshold as Float32Array)[
      entity
    ];
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
  ): Effect | null {
    const cs = Postprocessing as unknown as CS;
    if (!(cs.vignette as Uint8Array)[entity]) return null;
    return new VignetteEffect({
      blendFunction: BlendFunction.NORMAL,
      offset: (cs.vignetteOffset as Float32Array)[entity],
      darkness: (cs.vignetteDarkness as Float32Array)[entity],
    });
  },
  update(state: CS, entity: number, effect: Effect): void {
    const vignette = effect as VignetteEffect;
    vignette.offset = (state.vignetteOffset as Float32Array)[entity];
    vignette.darkness = (state.vignetteDarkness as Float32Array)[entity];
  },
});

registerEffect({
  key: 'ssao',
  create(
    _state: CS,
    entity: number,
    _renderer: WebGLRenderer,
    _scene: Scene,
    camera: Camera
  ): Effect | null {
    const cs = Postprocessing as unknown as CS;
    if (!(cs.ssao as Uint8Array)[entity]) return null;
    return new SSAOEffect(camera, undefined, {
      blendFunction: BlendFunction.MULTIPLY,
      intensity: (cs.ssaoIntensity as Float32Array)[entity],
      radius: (cs.ssaoRadius as Float32Array)[entity],
    });
  },
  update(state: CS, entity: number, effect: Effect): void {
    const ssao = effect as SSAOEffect;
    ssao.intensity = (state.ssaoIntensity as Float32Array)[entity];
    ssao.radius = Math.max(1e-6, (state.ssaoRadius as Float32Array)[entity]);
  },
});

registerEffect({
  key: 'depthOfField',
  create(
    _state: CS,
    entity: number,
    _renderer: WebGLRenderer,
    _scene: Scene,
    camera: Camera
  ): Effect | null {
    const cs = Postprocessing as unknown as CS;
    if (!(cs.depthOfField as Uint8Array)[entity]) return null;
    return new DepthOfFieldEffect(camera, {
      blendFunction: BlendFunction.NORMAL,
      focusDistance: (cs.dofFocusDistance as Float32Array)[entity],
      focusRange: (cs.dofFocusRange as Float32Array)[entity],
      bokehScale: (cs.dofBokehScale as Float32Array)[entity],
    });
  },
  update(state: CS, entity: number, effect: Effect): void {
    const dof = effect as DepthOfFieldEffect;
    dof.cocMaterial.focusDistance = (state.dofFocusDistance as Float32Array)[
      entity
    ];
    dof.cocMaterial.focusRange = (state.dofFocusRange as Float32Array)[entity];
    dof.bokehScale = (state.dofBokehScale as Float32Array)[entity];
  },
});

registerEffect({
  key: 'tonemapping',
  position: 'last',
  create(
    _state: CS,
    entity: number,
    _renderer: WebGLRenderer,
    _scene: Scene,
    _camera: Camera
  ): Effect | null {
    const cs = Postprocessing as unknown as CS;
    const idx = (cs.toneMapping as Uint8Array)[entity];
    if (idx === 0) return null;
    const mode = ToneMappingModes[Math.min(idx, ToneMappingModes.length) - 1];
    return new ToneMappingEffect({
      blendFunction: BlendFunction.NORMAL,
      mode,
    });
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
  ): Effect | null {
    const cs = Postprocessing as unknown as CS;
    if (!(cs.chromaticAberration as Uint8Array)[entity]) return null;
    const strength = (cs.caStrength as Float32Array)[entity];
    return new ChromaticAberrationEffect({
      blendFunction: BlendFunction.NORMAL,
      offset: new Vector2(strength, strength),
      radialModulation: false,
      modulationOffset: 0.15,
    });
  },
  update(state: CS, entity: number, effect: Effect): void {
    const ca = effect as ChromaticAberrationEffect;
    const strength = (state.caStrength as Float32Array)[entity];
    ca.offset = new Vector2(strength, strength);
  },
});

export function registerBuiltinEffects(): void {}
