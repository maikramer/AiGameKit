import { Vector2, type Camera, type Scene, type WebGLRenderer } from 'three';
import {
  BloomEffect,
  BrightnessContrastEffect,
  ChromaticAberrationEffect,
  DepthOfFieldEffect,
  EffectPass,
  FXAAEffect,
  HueSaturationEffect,
  SMAAEffect,
  SMAAPreset,
  ToneMappingEffect,
  ToneMappingMode,
  VignetteEffect,
  type Effect,
  type Pass,
} from 'postprocessing';
import { N8AOPostPass } from 'n8ao';
import { getGpuTierForRenderer } from '../rendering/utils';
import { Postprocessing } from './components';
import { registerEffect } from './effect-registry';

type CS = Record<string, Float32Array | Uint8Array>;

/**
 * Maps the component's toneMapping enum (0=off,1=AgX,2=ACES,3=Neutral,
 * 4=Reinhard) to the library's ToneMappingMode. Index 0 is handled by the
 * create() returning null (no tone-mapping pass).
 */
const ToneMappingModes = [
  ToneMappingMode.AGX,
  ToneMappingMode.ACES_FILMIC,
  ToneMappingMode.NEUTRAL,
  ToneMappingMode.REINHARD2,
] as const;

/**
 * EffectPass keeps its `effects` array private, so to update an effect's
 * parameters each frame we hold the Effect reference alongside the pass that
 * wraps it. SSAO uses N8AOPostPass directly (no EffectPass wrapper) and is
 * resolved by casting in its own update().
 */
const effectByPass = new WeakMap<Pass, Effect>();

function wrap(camera: Camera, effect: Effect): Pass {
  const pass = new EffectPass(camera, effect);
  effectByPass.set(pass, effect);
  return pass;
}

function effectOf(pass: Pass): Effect | undefined {
  return effectByPass.get(pass);
}

/** Same idea as {@link effectByPass} but for passes wrapping more than one
 * effect (color grading combines hue/saturation + brightness/contrast in a
 * single EffectPass so they share one shader compile). */
const multiEffectByPass = new WeakMap<Pass, Effect[]>();

function wrapMulti(camera: Camera, effects: Effect[]): Pass {
  const pass = new EffectPass(camera, ...effects);
  multiEffectByPass.set(pass, effects);
  return pass;
}

/** Maps a `detect-gpu` tier (0-3, undefined = unresolved yet) to n8ao's sample-count preset. */
function qualityModeForTier(
  tier: { tier: number } | null
): 'Performance' | 'Low' | 'Medium' | 'High' | 'Ultra' {
  if (!tier) return 'Medium';
  if (tier.tier <= 0) return 'Performance';
  if (tier.tier === 1) return 'Low';
  if (tier.tier === 2) return 'Medium';
  return 'High';
}

registerEffect({
  key: 'smaa',
  position: 'first',
  create(
    _state: CS,
    entity: number,
    _renderer: WebGLRenderer,
    _scene: Scene,
    camera: Camera
  ): Pass | null {
    const cs = Postprocessing as unknown as CS;
    if ((cs.aa as Uint8Array)[entity] !== 2) return null;
    return wrap(camera, new SMAAEffect({ preset: SMAAPreset.HIGH }));
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
    camera: Camera
  ): Pass | null {
    const cs = Postprocessing as unknown as CS;
    if ((cs.aa as Uint8Array)[entity] !== 1) return null;
    return wrap(camera, new FXAAEffect());
  },
});

registerEffect({
  key: 'bloom',
  create(
    _state: CS,
    entity: number,
    _renderer: WebGLRenderer,
    _scene: Scene,
    camera: Camera
  ): Pass | null {
    const cs = Postprocessing as unknown as CS;
    if (!(cs.bloom as Uint8Array)[entity]) return null;
    const bloom = new BloomEffect({
      mipmapBlur: true,
      luminanceThreshold: (cs.bloomThreshold as Float32Array)[entity],
      intensity: (cs.bloomStrength as Float32Array)[entity],
      radius: (cs.bloomRadius as Float32Array)[entity],
    });
    return wrap(camera, bloom);
  },
  update(state: CS, entity: number, pass: Pass): void {
    const bloom = effectOf(pass) as BloomEffect | undefined;
    if (!bloom) return;
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
    camera: Camera
  ): Pass | null {
    const cs = Postprocessing as unknown as CS;
    if (!(cs.vignette as Uint8Array)[entity]) return null;
    const vignette = new VignetteEffect({
      offset: (cs.vignetteOffset as Float32Array)[entity],
      darkness: (cs.vignetteDarkness as Float32Array)[entity],
    });
    return wrap(camera, vignette);
  },
  update(state: CS, entity: number, pass: Pass): void {
    const vignette = effectOf(pass) as VignetteEffect | undefined;
    if (!vignette) return;
    vignette.offset = (state.vignetteOffset as Float32Array)[entity];
    vignette.darkness = (state.vignetteDarkness as Float32Array)[entity];
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
    const n8ao = new N8AOPostPass(scene, camera, size.x, size.y);
    n8ao.configuration.aoRadius = Math.max(
      1e-6,
      (cs.ssaoRadius as Float32Array)[entity]
    );
    n8ao.configuration.intensity = Math.max(
      0,
      (cs.ssaoIntensity as Float32Array)[entity]
    );
    n8ao.setQualityMode(qualityModeForTier(getGpuTierForRenderer(renderer)));
    return n8ao as unknown as Pass;
  },
  update(state: CS, entity: number, pass: Pass): void {
    const ssao = pass as unknown as N8AOPostPass;
    ssao.configuration.aoRadius = Math.max(
      1e-6,
      (state.ssaoRadius as Float32Array)[entity]
    );
    ssao.configuration.intensity = Math.max(
      0,
      (state.ssaoIntensity as Float32Array)[entity]
    );
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
  ): Pass | null {
    const cs = Postprocessing as unknown as CS;
    if (!(cs.depthOfField as Uint8Array)[entity]) return null;
    const dof = new DepthOfFieldEffect(camera, {
      focusDistance: (cs.dofFocusDistance as Float32Array)[entity],
      focusRange: (cs.dofFocusRange as Float32Array)[entity],
      bokehScale: (cs.dofBokehScale as Float32Array)[entity],
    });
    return wrap(camera, dof);
  },
  update(state: CS, entity: number, pass: Pass): void {
    const dof = effectOf(pass) as DepthOfFieldEffect | undefined;
    if (!dof) return;
    dof.cocMaterial.focusDistance = (state.dofFocusDistance as Float32Array)[
      entity
    ];
    dof.cocMaterial.focusRange = (state.dofFocusRange as Float32Array)[entity];
    // bokehScale is a target-circle count; use the component's coarse scale
    // divided down so the default (~3) lands near the library default (1).
    dof.bokehScale = (state.dofBokehScale as Float32Array)[entity] / 3;
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
    camera: Camera
  ): Pass | null {
    const cs = Postprocessing as unknown as CS;
    const idx = (cs.toneMapping as Uint8Array)[entity];
    if (idx === 0) return null;
    const mode = ToneMappingModes[Math.min(idx, ToneMappingModes.length) - 1];
    return wrap(camera, new ToneMappingEffect({ mode }));
  },
});

registerEffect({
  key: 'chromaticAberration',
  create(
    _state: CS,
    entity: number,
    _renderer: WebGLRenderer,
    _scene: Scene,
    camera: Camera
  ): Pass | null {
    const cs = Postprocessing as unknown as CS;
    if (!(cs.chromaticAberration as Uint8Array)[entity]) return null;
    const strength = (cs.caStrength as Float32Array)[entity];
    const ca = new ChromaticAberrationEffect({
      offset: new Vector2(strength, strength),
      radialModulation: true,
      modulationOffset: 0.15,
    });
    return wrap(camera, ca);
  },
  update(state: CS, entity: number, pass: Pass): void {
    const ca = effectOf(pass) as ChromaticAberrationEffect | undefined;
    if (!ca) return;
    const strength = (state.caStrength as Float32Array)[entity];
    ca.offset.set(strength, strength);
  },
});

registerEffect({
  key: 'colorGrading',
  position: 'last',
  create(
    _state: CS,
    entity: number,
    _renderer: WebGLRenderer,
    _scene: Scene,
    camera: Camera
  ): Pass | null {
    const cs = Postprocessing as unknown as CS;
    if (!(cs.colorGrading as Uint8Array)[entity]) return null;
    const hueSaturation = new HueSaturationEffect({
      saturation: (cs.saturation as Float32Array)[entity],
    });
    const brightnessContrast = new BrightnessContrastEffect({
      brightness: (cs.brightness as Float32Array)[entity],
      contrast: (cs.contrast as Float32Array)[entity],
    });
    return wrapMulti(camera, [hueSaturation, brightnessContrast]);
  },
  update(state: CS, entity: number, pass: Pass): void {
    const effects = multiEffectByPass.get(pass);
    if (!effects) return;
    const hueSaturation = effects[0] as HueSaturationEffect;
    const brightnessContrast = effects[1] as BrightnessContrastEffect;
    hueSaturation.saturation = (state.saturation as Float32Array)[entity];
    brightnessContrast.brightness = (state.brightness as Float32Array)[entity];
    brightnessContrast.contrast = (state.contrast as Float32Array)[entity];
  },
});

export function registerBuiltinEffects(): void {}
