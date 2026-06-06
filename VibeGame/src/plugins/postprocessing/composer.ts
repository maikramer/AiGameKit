import { HalfFloatType, FloatType, UnsignedByteType } from 'three';
import type { WebGLRenderer, Scene, Camera } from 'three';
import { EffectComposer, EffectPass, RenderPass } from 'postprocessing';
import type { Effect } from 'postprocessing';

export type PostProcessingPipeline = EffectComposer;

function resolveFrameBufferType(renderer: WebGLRenderer): number {
  const gl = renderer.getContext() as WebGL2RenderingContext;
  // Half-float framebuffers require EXT_color_buffer_half_float (WebGL2) or
  // the equivalent color-renderable format in the WebGL2 internal table.
  if (
    gl.getExtension('EXT_color_buffer_half_float') ??
    gl.getExtension('OES_texture_half_float')
  ) {
    return HalfFloatType;
  }
  // Full float is widely supported on desktop.
  if (gl.getExtension('EXT_color_buffer_float')) {
    return FloatType;
  }
  return UnsignedByteType;
}

export function buildComposer(
  renderer: WebGLRenderer,
  scene: Scene,
  camera: Camera,
  effects: Effect[],
  convolutionEffects: Effect[]
): EffectComposer {
  const composer = new EffectComposer(renderer, {
    frameBufferType: resolveFrameBufferType(renderer),
  });

  composer.addPass(new RenderPass(scene, camera));

  if (convolutionEffects.length > 0) {
    composer.addPass(new EffectPass(camera, ...convolutionEffects));
  }

  if (effects.length > 0) {
    composer.addPass(new EffectPass(camera, ...effects));
  }

  return composer;
}
