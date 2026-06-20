import { NoToneMapping, type ToneMapping } from 'three';
import { defineQuery, type State, type System } from '../../core';
import { CameraSyncSystem } from '../rendering/systems';
import { getRenderingContext, threeCameras } from '../rendering/utils';
import { MainCamera } from '../rendering/components';
import { Postprocessing } from './components';
import { registerBuiltinEffects } from './builtin-effects';
import { type EffectDefinition, getEffectDefinitions } from './effect-registry';
import { buildComposer } from './composer';
import type { Effect } from 'postprocessing';

const postprocessingQuery = defineQuery([Postprocessing]);
const mainCameraQuery = defineQuery([MainCamera]);

let builtinEffectsRegistered = false;
/** Renderer tone mapping captured before the composer takes ownership of it. */
let savedRendererToneMapping: ToneMapping | null = null;

const activeEffectInstances: Array<{
  def: EffectDefinition;
  effect: Effect;
  entity: number;
}> = [];

/** Changing renderer.toneMapping only affects newly compiled programs. */
function invalidateSceneMaterials(scene: import('three').Scene): void {
  scene.traverse((obj) => {
    const mesh = obj as {
      material?: { needsUpdate: boolean } | { needsUpdate: boolean }[];
    };
    if (!mesh.material) return;
    if (Array.isArray(mesh.material)) {
      for (const m of mesh.material) m.needsUpdate = true;
    } else {
      mesh.material.needsUpdate = true;
    }
  });
}

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
    const regularEffects: Effect[] = [];
    const convolutionEffects: Effect[] = [];

    activeEffectInstances.length = 0;
    for (const def of getEffectDefinitions()) {
      const effect = def.create(
        componentState,
        e,
        context.renderer!,
        context.scene,
        camera
      );
      if (!effect) continue;

      activeEffectInstances.push({ def, effect, entity: e });

      if (def.key === 'chromaticAberration') {
        convolutionEffects.push(effect);
      } else {
        regularEffects.push(effect);
      }
    }

    if (regularEffects.length === 0 && convolutionEffects.length === 0) return;

    context.postProcessing = buildComposer(
      context.renderer,
      context.scene,
      camera,
      regularEffects,
      convolutionEffects
    );

    // Tone mapping must happen exactly once. When the composer carries a
    // ToneMappingEffect, the scene must reach it linear/HDR — leaving the
    // renderer's own tone mapping on would apply the curve twice and wash the
    // image out (flat contrast, desaturated colors).
    const usesToneMappingEffect = Postprocessing.toneMapping[e] !== 0;
    if (usesToneMappingEffect) {
      if (savedRendererToneMapping === null) {
        savedRendererToneMapping = context.renderer.toneMapping;
      }
      if (context.renderer.toneMapping !== NoToneMapping) {
        context.renderer.toneMapping = NoToneMapping;
        invalidateSceneMaterials(context.scene);
      }
    } else if (savedRendererToneMapping !== null) {
      context.renderer.toneMapping = savedRendererToneMapping;
      savedRendererToneMapping = null;
      invalidateSceneMaterials(context.scene);
    }
  },
  dispose(state: State) {
    const context = getRenderingContext(state);
    context.postProcessing?.dispose();
    context.postProcessing = undefined;
    activeEffectInstances.length = 0;
    if (context.renderer && savedRendererToneMapping !== null) {
      context.renderer.toneMapping = savedRendererToneMapping;
      savedRendererToneMapping = null;
    }
  },
};

export const PostprocessingEffectUpdateSystem: System = {
  group: 'draw',
  after: [PostprocessingBuildSystem, CameraSyncSystem],
  update(state: State) {
    if (state.headless) return;
    if (activeEffectInstances.length === 0) return;
    const componentState = Postprocessing as unknown as Record<
      string,
      Float32Array | Uint8Array
    >;
    for (const { def, effect, entity } of activeEffectInstances) {
      if (!def.update) continue;
      try {
        def.update(componentState, entity, effect);
      } catch (err) {
        // Effect update errors must not crash the render loop.
        if (typeof console !== 'undefined') {
          console.error(
            `[VibeGame] Postprocessing effect "${def.key}" update threw:`,
            err
          );
        }
      }
    }
  },
};
