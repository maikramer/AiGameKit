import { defineQuery, type State, type System } from '../../core';
import { CameraSyncSystem } from '../rendering/systems';
import { getRenderingContext, threeCameras } from '../rendering/utils';
import { MainCamera } from '../rendering/components';
import { Postprocessing } from './components';
import { registerBuiltinEffects } from './builtin-effects';
import { getEffectDefinitions } from './effect-registry';
import { buildComposer } from './composer';
import type { Effect } from 'postprocessing';

const postprocessingQuery = defineQuery([Postprocessing]);
const mainCameraQuery = defineQuery([MainCamera]);

let builtinEffectsRegistered = false;

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

    for (const def of getEffectDefinitions()) {
      const effect = def.create(
        componentState,
        e,
        context.renderer!,
        context.scene,
        camera
      );
      if (!effect) continue;

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
  },
  dispose(state: State) {
    const context = getRenderingContext(state);
    context.postProcessing?.dispose();
    context.postProcessing = undefined;
  },
};
