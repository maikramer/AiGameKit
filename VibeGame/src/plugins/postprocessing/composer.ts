import { HalfFloatType, Vector2 } from 'three';
import type { Camera, Scene, WebGLRenderer } from 'three';
import { EffectComposer, RenderPass } from 'postprocessing';
import type { Pass } from 'postprocessing';

export type PostProcessingPipeline = EffectComposer;

const _size = new Vector2();

/**
 * Ensure composer input/output/depth targets match the renderer.
 *
 * EffectComposer constructs buffers at 0×0 while `renderer` is still null,
 * then `setRenderer` sizes them from `renderer.getSize()`. If that size was
 * still 0 (canvas not laid out) — or a later resize was missed — warmup /
 * first draws hit WebGL `FRAMEBUFFER_INCOMPLETE_ATTACHMENT` on the depth
 * attachment. Returns false when no positive size is available yet.
 */
export function syncComposerSize(
  composer: { setSize(width: number, height: number): void },
  renderer: WebGLRenderer
): boolean {
  renderer.getSize(_size);
  let width = Math.floor(_size.x);
  let height = Math.floor(_size.y);
  if (width <= 0 || height <= 0) {
    const canvas = renderer.domElement;
    width = Math.floor(
      canvas?.clientWidth ||
        (typeof window !== 'undefined' ? window.innerWidth : 0)
    );
    height = Math.floor(
      canvas?.clientHeight ||
        (typeof window !== 'undefined' ? window.innerHeight : 0)
    );
    if (width > 0 && height > 0) {
      renderer.setSize(width, height, false);
    }
  }
  if (width <= 0 || height <= 0) return false;
  composer.setSize(width, height);
  // Drawing-buffer can still read 0 in Firefox before the first present —
  // CSS/logical size is enough for EffectComposer targets via setSize.
  return true;
}

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

  // Re-sync after passes attach — ctor may have sized from a 0×0 drawing buffer.
  syncComposerSize(composer, renderer);

  return composer;
}
