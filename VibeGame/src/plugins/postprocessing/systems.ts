import {
  Color,
  FogExp2,
  NoToneMapping,
  type Scene,
  type ToneMapping,
  type WebGLRenderer,
} from 'three';
import type { Pass } from 'postprocessing';
import { defineQuery, type State, type System } from '../../core';
import { CameraSyncSystem } from '../rendering/systems';
import { getRenderingContext, threeCameras } from '../rendering/utils';
import { MainCamera } from '../rendering/components';
import { Postprocessing } from './components';
import {
  disposeSharedSunMesh,
  registerBuiltinEffects,
} from './builtin-effects';
import { type EffectDefinition, getEffectDefinitions } from './effect-registry';
import { buildComposer } from './composer';

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

export const PostprocessingBuildSystem: System = {
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

    const componentState = Postprocessing as unknown as Record<
      string,
      Float32Array | Uint8Array
    >;

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
};

export const PostprocessingEffectUpdateSystem: System = {
  group: 'draw',
  after: [PostprocessingBuildSystem, CameraSyncSystem],
  update(state: State) {
    if (state.headless) return;
    if (activeEffectInstances.length === 0) return;
    const context = getRenderingContext(state);
    const componentState = Postprocessing as unknown as Record<
      string,
      Float32Array | Uint8Array
    >;
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
};

/**
 * Applies the `Postprocessing` height-fog fields (`fogColor` / `fogDensity`) to
 * `scene.fog`. The biomes plugin crossfades these values every frame as the
 * player crosses biome borders; without this system they were written into the
 * component but never consumed — a half-wired feature. `FogExp2` gives a
 * depth-driven exponential falloff that reads as atmospheric haze and gives
 * `GodRaysEffect` a medium to scatter through.
 *
 * `fogHeight` / `fogFalloff` are kept on the component for future height-based
 * modulation (the three.js core `FogExp2` is density-uniform in world space);
 * for now density is taken as-is, which already matches the values the biomes
 * plugin interpolates.
 */
interface FogCache {
  color: number;
  density: number;
}
const fogCacheByScene = new WeakMap<Scene, FogCache>();
const _fogColor = new Color();

export const FogSyncSystem: System = {
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
};
