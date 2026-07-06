import type { Camera, Mesh, Scene, WebGLRenderer } from 'three';
import { WebGLRenderTarget } from 'three';
import { SSRPass } from 'three/examples/jsm/postprocessing/SSRPass.js';
import { Pass } from 'postprocessing';
import { logger } from '../../core/utils/logger';

/**
 * Adapter that wraps three's `SSRPass` (from `three/examples/jsm/postprocessing/`)
 * so it can be inserted into the **pmndrs** `EffectComposer` that VibeGame uses.
 *
 * The two libraries have incompatible `Pass` interfaces:
 *  - three's `Pass`: `render(renderer, writeBuffer, readBuffer?)`, owns its own
 *    scene re-render for normals/depth/metalness, writes a combined result.
 *  - pmndrs `Pass`: `render(renderer, inputBuffer, outputBuffer?, ...)`, chain
 *    of fullscreen effects reading the previous pass's color buffer.
 *
 * `SSRPass` is closer to a self-contained effect: it samples the beauty buffer
 * (which it renders itself) + depth + normals + metalness and writes a blended
 * reflection into `writeBuffer`. The adapter delegates to the wrapped pass and
 * adapts the render signature; if the wrapped pass throws, the adapter goes
 * inert and the composer continues without SSR rather than crashing the frame
 * loop.
 *
 * IMPORTANT: this extends the pmndrs `Pass` base class so the methods
 * `EffectComposer.addPass()` calls (`initialize()`, `setDepthTexture()`, the
 * `depthTexture`/`renderer`/`scene`/`camera` getters, …) resolve to the working
 * default implementations the base provides — only `render`/`setSize`/`dispose`
 * are overridden.
 *
 * SSR is expensive (re-renders the scene for normals + depth + metalness + the
 * SSR ray-march). Keep the resolution scale below 1 on laptops.
 */
export interface SSRAdapterOptions {
  renderer: WebGLRenderer;
  scene: Scene;
  camera: Camera;
  width: number;
  height: number;
  /** Meshes that receive reflections. SSRPass renders these into its
   * metalness mask and reflects ONLY on their pixels; everything else stays
   * untouched. Pass a persistent array and mutate it in place — SSRPass reads
   * it live each render. `null`/undefined would reflect the whole scene,
   * which mirrors grass onto house walls — always pass an array. */
  selects: Mesh[];
  bouncing?: boolean;
}

type SSRPassLike = InstanceType<typeof SSRPass>;

export class SSRPassAdapter extends Pass {
  private ssrPass: SSRPassLike | null;

  private disposed = false;

  constructor(opts: SSRAdapterOptions) {
    super('SSRPassAdapter');
    this.needsSwap = true;
    this.needsDepthTexture = false;
    try {
      // SSRPass requires a `groundReflector` field (nullable). Pass null so SSR
      // applies screen-space without a dedicated ground reflector mesh.
      this.ssrPass = new SSRPass({
        renderer: opts.renderer,
        scene: opts.scene,
        camera: opts.camera,
        width: opts.width,
        height: opts.height,
        selects: opts.selects,
        isBouncing: opts.bouncing ?? false,
        groundReflector: null,
      });
    } catch (err) {
      logger.warn(
        '[ssr] Failed to construct SSRPass — SSR effect disabled, falling back to no SSR',
        err
      );
      this.ssrPass = null;
      this.enabled = false;
    }
  }

  /**
   * pmndrs `Pass.render` signature uses nullable buffers (`inputBuffer | null`).
   * We delegate to the wrapped SSRPass which expects `(renderer, writeBuffer)`.
   * If the wrapped pass is unavailable or throws, the adapter goes inert.
   */
  override render(
    renderer: WebGLRenderer,
    inputBuffer: WebGLRenderTarget | null,
    outputBuffer: WebGLRenderTarget | null
  ): void {
    if (!this.enabled || !this.ssrPass) {
      if (outputBuffer && outputBuffer !== inputBuffer) {
        renderer.setRenderTarget(outputBuffer);
      }
      return;
    }
    try {
      // SSRPass.render(renderer, writeBuffer, readBuffer, deltaTime, maskActive)
      // — it owns its own beauty/depth re-render and blends reflections onto
      // writeBuffer. readBuffer is unused by SSRPass internally.
      const target = outputBuffer ?? inputBuffer;
      this.ssrPass.render(
        renderer,
        target as WebGLRenderTarget,
        target as WebGLRenderTarget,
        0,
        false
      );
    } catch (err) {
      logger.warn('[ssr] SSRPass.render threw — disabling SSR adapter', err);
      this.enabled = false;
    }
  }

  override setSize(width: number, height: number): void {
    try {
      this.ssrPass?.setSize(width, height);
    } catch {
      /* best-effort */
    }
  }

  /** Expose the wrapped pass so the effect registration can tune uniforms. */
  get wrapped(): SSRPassLike | null {
    return this.ssrPass;
  }

  override dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    try {
      this.ssrPass?.dispose();
    } catch {
      /* best-effort */
    }
    this.ssrPass = null;
    super.dispose();
  }
}
