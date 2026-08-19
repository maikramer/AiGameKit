import {
  Color,
  FogExp2,
  NoToneMapping,
  type Scene,
  type ToneMapping,
  type WebGLRenderer,
} from 'three';
import type { Pass } from 'postprocessing';
import { defineSystem, defineQuery, type State, type System } from '../../core';
import { CameraSyncSystem } from '../rendering/systems';
import { getRenderingContext, threeCameras } from '../rendering/utils';
import { MainCamera } from '../rendering/components';
import { Postprocessing } from './components';
import {
  disposeSharedSunMesh,
  registerBuiltinEffects,
} from './builtin-effects';
import {
  type EffectComponentState,
  type EffectDefinition,
  getEffectDefinitions,
} from './effect-registry';
import { buildComposer } from './composer';
import type { ReflectionPass } from './reflection-pass';
import { N8AOPostPass } from 'n8ao';
import {
  getAdaptiveQualityTier,
  TIER_PRESETS,
} from '../adaptive-quality/quality-tiers';
import type { GodRaysEffect, DepthOfFieldEffect } from 'postprocessing';

const postprocessingQuery = defineQuery([Postprocessing]);
const mainCameraQuery = defineQuery([MainCamera]);

let builtinEffectsRegistered = false;

/**
 * Renderer → original `toneMapping` value saved before the build step switched
 * it to `NoToneMapping` so the composer's `ToneMappingEffect` pass wouldn't
 * double-apply tone mapping. Restored on dispose.
 */
const savedToneMappingByRenderer = new WeakMap<WebGLRenderer, ToneMapping>();

/**
 * Last-applied `toneMappingExposure` per renderer. The sync system writes the
 * component value to `renderer.toneMappingExposure` only when it changes so the
 * uniform isn't touched every frame.
 */
const lastExposureByRenderer = new WeakMap<WebGLRenderer, number>();

const activeEffectInstances: Array<{
  def: EffectDefinition;
  pass: Pass;
  entity: number;
}> = [];

export const PostprocessingBuildSystem: System = defineSystem({
  name: 'PostprocessingBuildSystem',
  group: 'draw',
  after: [CameraSyncSystem],
  update(state: State) {
    if (state.headless) return;
    const context = getRenderingContext(state);
    if (context.postProcessing || !context.renderer) return;

    const entities = postprocessingQuery(state.world);
    if (entities.length === 0) return;
    const e = entities[0];
    if (Postprocessing.enabled[e] !== 1) return;

    const cameras = mainCameraQuery(state.world);
    if (cameras.length === 0) return;
    const camera = threeCameras.get(cameras[0]);
    if (!camera) return;

    if (!builtinEffectsRegistered) {
      registerBuiltinEffects();
      builtinEffectsRegistered = true;
    }

    const componentState = Postprocessing as unknown as EffectComponentState;

    const firstPasses: Array<{ pass: Pass; order: number }> = [];
    const regularPasses: Array<{ pass: Pass; order: number }> = [];
    const lastPasses: Array<{ pass: Pass; order: number }> = [];

    activeEffectInstances.length = 0;
    let hasToneMappingPass = false;
    for (const def of getEffectDefinitions()) {
      const pass = def.create(
        componentState,
        e,
        context.renderer,
        context.scene,
        camera
      );
      if (!pass) continue;

      if (def.key === 'tonemapping') hasToneMappingPass = true;

      activeEffectInstances.push({ def, pass, entity: e });

      const entry = { pass, order: def.order ?? 0 };
      if (def.position === 'first') {
        firstPasses.push(entry);
      } else if (def.position === 'last') {
        lastPasses.push(entry);
      } else {
        regularPasses.push(entry);
      }
    }

    // Sort each bucket by `order` (stable: ties keep registration order) so a
    // scene-re-rendering pass like SSR can run before the AA passes that share
    // `position: 'first'`.
    const byOrder = (a: { order: number }, b: { order: number }) =>
      a.order - b.order;
    firstPasses.sort(byOrder);
    regularPasses.sort(byOrder);
    lastPasses.sort(byOrder);
    const orderedPasses = [...firstPasses, ...regularPasses, ...lastPasses].map(
      (entry) => entry.pass
    );
    if (orderedPasses.length === 0) return;

    // The renderer defaults to AgXToneMapping (rendering/utils.ts), AND a
    // ToneMappingEffect pass applies tone mapping again in the composer. When
    // both run the image is tone-mapped twice (washed/burnt). The pass owns
    // tone mapping inside the composer, so switch the renderer to NoToneMapping
    // for the composer's lifetime and restore on dispose. If there is no tone-
    // mapping pass this stays a no-op so non-tone-mapped pipelines keep the
    // renderer default.
    if (hasToneMappingPass && context.renderer.toneMapping !== NoToneMapping) {
      savedToneMappingByRenderer.set(
        context.renderer,
        context.renderer.toneMapping
      );
      context.renderer.toneMapping = NoToneMapping;
    }

    context.postProcessing = buildComposer(
      context.renderer,
      context.scene,
      camera,
      orderedPasses
    );
  },
  dispose(state: State) {
    const context = getRenderingContext(state);
    context.postProcessing?.dispose();
    context.postProcessing = undefined;
    activeEffectInstances.length = 0;
    // Drop the shared GodRays sun mesh so a scene reload doesn't leak it.
    disposeSharedSunMesh();
    // Restore the renderer's tone mapping that the build step disabled so the
    // composer's ToneMappingEffect wouldn't double-apply it.
    const renderer = context.renderer;
    if (renderer && savedToneMappingByRenderer.has(renderer)) {
      const saved = savedToneMappingByRenderer.get(renderer);
      if (saved !== undefined) renderer.toneMapping = saved;
      savedToneMappingByRenderer.delete(renderer);
    }
  },
});

export const PostprocessingEffectUpdateSystem: System = defineSystem({
  name: 'PostprocessingEffectUpdateSystem',
  group: 'draw',
  after: [PostprocessingBuildSystem, CameraSyncSystem],
  update(state: State) {
    if (state.headless) return;
    if (activeEffectInstances.length === 0) return;
    const context = getRenderingContext(state);
    const componentState = Postprocessing as unknown as EffectComponentState;
    for (const { def, pass, entity } of activeEffectInstances) {
      if (!def.update) continue;
      try {
        def.update(componentState, entity, pass);
      } catch (err) {
        if (typeof console !== 'undefined') {
          console.error(
            `[VibeGame] Postprocessing effect "${def.key}" update threw:`,
            err
          );
        }
      }
    }

    // Adaptive Quality levers — apply per-tier presets to the effect passes.
    // The scaler measures frame time elsewhere and writes the tier; here we
    // honor it by downgrading expensive effect parameters. At tier 0 (Max)
    // every lever is at its full setting (no-op vs. the effect defaults), so
    // when the GPU has headroom nothing is degraded.
    applyAdaptiveEffectLevers(state);

    // Sync `toneMappingExposure` to the renderer. The composer's
    // ToneMappingEffect pass reads the renderer's exposure each frame, so XML
    // exposure overrides (`tone-mapping-exposure="1.15"`) reach the final
    // image. Dirty-gated to avoid touching the uniform when it didn't move.
    if (context.renderer) {
      const exposure =
        Postprocessing.toneMappingExposure[activeEffectInstances[0].entity];
      if (
        lastExposureByRenderer.get(context.renderer) !== exposure &&
        Number.isFinite(exposure) &&
        exposure > 0
      ) {
        context.renderer.toneMappingExposure = exposure;
        lastExposureByRenderer.set(context.renderer, exposure);
      }
    }
  },
});

/**
 * Apply Adaptive Quality tier presets to the live effect passes. Each lever is
 * a cheap property write gated on a tier change, so at steady state (no tier
 * transition) this is a handful of comparisons per frame.
 *
 * Levers applied:
 *  - SSAO: `configuration.halfRes` + intensity × tier scale
 *  - God rays: `godRaysMaterial.samples` (48 → 32 → 24 → 16)
 *  - DoF: `bokehScale` scaled by the tier factor (0 at tier 3 effectively
 *    disables the blur cost)
 *  - SSR: intensity × tier scale, and the whole pass switched off at tier Low
 *
 * Pixel-ratio scaling is handled directly by the Adaptive Quality apply system
 * (it owns the renderer). Point-shadow throttling is handled by the light sync
 * system. Water-mirror gating is handled by the water plugin.
 */
function applyAdaptiveEffectLevers(state: State): void {
  if (activeEffectInstances.length === 0) return;
  const tier = getAdaptiveQualityTier(state);
  const preset = TIER_PRESETS[tier] ?? TIER_PRESETS[0];

  for (const { def, pass, entity } of activeEffectInstances) {
    try {
      switch (def.key) {
        case 'ssao': {
          const n8ao = pass as unknown as N8AOPostPass;
          const base = Postprocessing.ssaoIntensity[entity];
          const intensity = Math.max(0, base * preset.ssaoIntensityScale);
          // Tier Low scales intensity to 0 — disable the pass so N8AO does not
          // keep filling an AO buffer for no visible benefit.
          const enabled = intensity > 1e-4 && Postprocessing.ssao[entity] === 1;
          const passWithEnabled = n8ao as unknown as { enabled?: boolean };
          if (passWithEnabled.enabled !== enabled) {
            passWithEnabled.enabled = enabled;
          }
          if (!enabled) break;
          if (n8ao?.configuration?.halfRes !== preset.ssaoHalfResolution) {
            n8ao.configuration.halfRes = preset.ssaoHalfResolution;
          }
          if (n8ao.configuration.intensity !== intensity) {
            n8ao.configuration.intensity = intensity;
          }
          break;
        }
        case 'godRays': {
          const gr = effectOfPass(pass) as GodRaysEffect | undefined;
          if (
            gr?.godRaysMaterial &&
            gr.godRaysMaterial.samples !== preset.godRaysSamples
          ) {
            gr.godRaysMaterial.samples = preset.godRaysSamples;
          }
          break;
        }
        case 'ssr': {
          const reflection = pass as unknown as ReflectionPass & {
            enabled?: boolean;
          };
          const base = Postprocessing.ssrOpacity[entity];
          const intensity = Math.max(0, base * preset.ssrIntensityScale);
          // At 0 the pass is switched off rather than left marching rays whose
          // result is multiplied away — the march is the expensive half.
          const enabled = intensity > 1e-4 && Postprocessing.ssr[entity] === 1;
          if (reflection.enabled !== enabled) reflection.enabled = enabled;
          if (!enabled) break;
          reflection.configure({
            intensity,
            maxDistance: Postprocessing.ssrMaxDistance[entity],
            thickness: Postprocessing.ssrThickness[entity],
          });
          break;
        }
        case 'depthOfField': {
          const dof = effectOfPass(pass) as DepthOfFieldEffect | undefined;
          if (dof) {
            // bokehScale is derived from the user's configured value × the tier
            // factor. We can't read the user's "intended" value back from the
            // effect reliably after scaling, so scale relative to the
            // component field each frame.
            const base = Postprocessing.dofBokehScale[entity] / 3;
            const bokehScale = Math.max(0, base * preset.dofBokehScaleScale);
            if (dof.bokehScale !== bokehScale) {
              dof.bokehScale = bokehScale;
            }
          }
          break;
        }
      }
    } catch {
      // An effect pass may have been disposed mid-frame; skip silently.
    }
  }
}

/** Resolve the underlying Effect wrapped by an EffectPass (mirrors the
 *  builtin-effects `effectOf` helper, kept local to avoid a cross-module
 *  export). Falls back to the pass itself for non-wrapped passes. */
const effectByPass = new WeakMap<Pass, unknown>();
function effectOfPass(pass: Pass): unknown {
  // The pmndrs EffectPass stores its effects privately; the builtin-effects
  // module keeps its own WeakMap. For god-rays/DoF the create() wraps via
  // `wrap()`, so the pass is an EffectPass. We attempt the public `effects`
  // getter if present, else return the pass (N8AO is not wrapped).
  const cached = effectByPass.get(pass);
  if (cached) return cached;
  const maybeEffects = (pass as unknown as { effects?: unknown[] }).effects;
  const resolved =
    Array.isArray(maybeEffects) && maybeEffects.length > 0
      ? maybeEffects[0]
      : pass;
  effectByPass.set(pass, resolved);
  return resolved;
}

/**
 * Applies the `Postprocessing` height-fog fields to the scene. When the
 * post-processing composer is active the `HeightFogEffect` pass owns the fog
 * (full height falloff, sun inscattering, sky haze) and `scene.fog` must stay
 * null so the two don't stack. Without a composer, `FogExp2` is the fallback so
 * scenes that skip post-processing still get distance haze. The biomes plugin
 * crossfades `fogColor`/`fogDensity` on the component, which both paths read.
 */
interface FogCache {
  color: number;
  density: number;
}
const fogCacheByScene = new WeakMap<Scene, FogCache>();
const _fogColor = new Color();

export const FogSyncSystem: System = defineSystem({
  name: 'FogSyncSystem',
  group: 'draw',
  after: [CameraSyncSystem],
  update(state: State) {
    if (state.headless) return;
    const context = getRenderingContext(state);
    const scene = context.scene;
    if (!scene) return;

    // Find the first entity with height-fog enabled (matches the biomes
    // plugin's `firstHeightFogEntity` convention — there is one global fog).
    let fogEntity = -1;
    for (const eid of postprocessingQuery(state.world)) {
      if (Postprocessing.heightFog[eid] === 1) {
        fogEntity = eid;
        break;
      }
    }

    // The composer's HeightFogEffect is the one true fog; material-level fog
    // on top of it would double the haze.
    if (context.postProcessing) {
      if (scene.fog) scene.fog = null;
      fogCacheByScene.delete(scene);
      return;
    }

    if (fogEntity === -1) {
      // No fog wanted this frame — drop any fog we previously applied.
      if (scene.fog) scene.fog = null;
      fogCacheByScene.delete(scene);
      return;
    }

    const color = Postprocessing.fogColor[fogEntity];
    const density = Postprocessing.fogDensity[fogEntity];

    let cache = fogCacheByScene.get(scene);
    if (!cache) {
      cache = { color: NaN, density: NaN };
      fogCacheByScene.set(scene, cache);
    }

    if (cache.color !== color || cache.density !== density || !scene.fog) {
      _fogColor.setHex(color);
      if (scene.fog instanceof FogExp2) {
        scene.fog.color.copy(_fogColor);
        scene.fog.density = density;
      } else {
        scene.fog = new FogExp2(_fogColor.getHex(), density);
      }
      cache.color = color;
      cache.density = density;
    }
  },
  dispose(state: State) {
    if (state.headless) return;
    const context = getRenderingContext(state);
    if (context.scene) {
      context.scene.fog = null;
      fogCacheByScene.delete(context.scene);
    }
  },
});
