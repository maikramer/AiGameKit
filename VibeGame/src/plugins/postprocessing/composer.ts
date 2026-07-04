import { HalfFloatType } from 'three';
import type { Camera, Scene, WebGLRenderer } from 'three';
import { EffectComposer, RenderPass } from 'postprocessing';
import type { Pass } from 'postprocessing';

export type PostProcessingPipeline = EffectComposer;

/**
 * Build a post-processing pipeline around the `postprocessing` library's
 * EffectComposer. The composer owns half-float frame buffers and (optionally)
 * MSAA; callers add RenderPass + their effect passes.
 *
 * Note: passes here are the library's own `Pass` base (EffectPass wrapping an
 * Effect, N8AOPostPass, etc.) — not the three-examples Pass.
 */
export function buildComposer(
  renderer: WebGLRenderer,
  scene: Scene,
  camera: Camera,
  passes: Pass[]
): EffectComposer {
  const composer = new EffectComposer(renderer, {
    frameBufferType: HalfFloatType,
    depthBuffer: true,
  });

  composer.addPass(new RenderPass(scene, camera));

  for (const pass of passes) {
    composer.addPass(pass);
  }

  return composer;
}
