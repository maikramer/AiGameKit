import {
  FloatType,
  HalfFloatType,
  UnsignedByteType,
  Vector2,
  WebGLRenderTarget,
} from 'three';
import type { Camera, Scene, TextureDataType, WebGLRenderer } from 'three';
import { EffectComposer } from 'three/examples/jsm/postprocessing/EffectComposer.js';
import { OutputPass } from 'three/examples/jsm/postprocessing/OutputPass.js';
import { RenderPass } from 'three/examples/jsm/postprocessing/RenderPass.js';
import type { Pass } from 'three/examples/jsm/postprocessing/Pass.js';

export type PostProcessingPipeline = EffectComposer;

function resolveFrameBufferType(renderer: WebGLRenderer): TextureDataType {
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
  passes: Pass[]
): EffectComposer {
  const frameBufferType = resolveFrameBufferType(renderer);
  const size = renderer.getSize(new Vector2());
  const pixelRatio = renderer.getPixelRatio();
  const renderTarget = new WebGLRenderTarget(
    Math.max(1, Math.floor(size.width * pixelRatio)),
    Math.max(1, Math.floor(size.height * pixelRatio)),
    { type: frameBufferType }
  );

  const composer = new EffectComposer(renderer, renderTarget);

  composer.addPass(new RenderPass(scene, camera));

  for (const pass of passes) {
    composer.addPass(pass);
  }

  composer.addPass(new OutputPass());

  return composer;
}
