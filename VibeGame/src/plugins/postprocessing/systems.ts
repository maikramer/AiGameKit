import type { Scene, ToneMapping } from 'three';
import type { Pass } from 'three/examples/jsm/postprocessing/Pass.js';
import { defineQuery, type State, type System } from '../../core';
import { CameraSyncSystem } from '../rendering/systems';
import { getRenderingContext, threeCameras } from '../rendering/utils';
import { MainCamera } from '../rendering/components';
import { Postprocessing } from './components';
import { registerBuiltinEffects } from './builtin-effects';
import { type EffectDefinition, getEffectDefinitions } from './effect-registry';
import { buildComposer } from './composer';

const postprocessingQuery = defineQuery([Postprocessing]);
const mainCameraQuery = defineQuery([MainCamera]);

let builtinEffectsRegistered = false;
let savedRendererToneMapping: ToneMapping | null = null;

const activeEffectInstances: Array<{
  def: EffectDefinition;
  pass: Pass;
  entity: number;
}> = [];

function invalidateSceneMaterials(scene: Scene): void {
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

    // Save the renderer's original tone mapping before any definition mutates it.
    // The tonemapping definition sets renderer.toneMapping as a side effect of
    // create(); OutputPass then applies that value at render time.
    const usesToneMapping = Postprocessing.toneMapping[e] !== 0;
    if (usesToneMapping) {
      if (savedRendererToneMapping === null) {
        savedRendererToneMapping = context.renderer.toneMapping;
      }
    } else if (savedRendererToneMapping !== null) {
      context.renderer.toneMapping = savedRendererToneMapping;
      savedRendererToneMapping = null;
      invalidateSceneMaterials(context.scene);
    }

    const firstPasses: Pass[] = [];
    const regularPasses: Pass[] = [];
    const lastPasses: Pass[] = [];

    activeEffectInstances.length = 0;
    for (const def of getEffectDefinitions()) {
      const pass = def.create(
        componentState,
        e,
        context.renderer,
        context.scene,
        camera
      );
      if (!pass) continue;

      activeEffectInstances.push({ def, pass, entity: e });

      if (def.position === 'first') {
        firstPasses.push(pass);
      } else if (def.position === 'last') {
        lastPasses.push(pass);
      } else {
        regularPasses.push(pass);
      }
    }

    const orderedPasses = [...firstPasses, ...regularPasses, ...lastPasses];
    if (orderedPasses.length === 0) return;

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
  },
};
