/**
 * Skymap2D / equirect PNG → ``Scene.environment`` (PMREM) para PBR no browser.
 */
import * as THREE from 'three';

import type { State } from '../core';
import { getRenderingContext, getScene } from '../plugins/rendering';

export interface EquirectSkyOptions {
  /** Se true (defeito), também define ``scene.background``; se false, só iluminação IBL. */
  background?: boolean;
}

/**
 * Carrega textura equirectangular (PNG/JPG) e aplica PMREM como ambiente IBL.
 *
 * Requer runtime com canvas (``renderer`` inicializado). Típico: após ``run()``.
 */
export async function applyEquirectSkyEnvironment(
  state: State,
  url: string,
  options?: EquirectSkyOptions
): Promise<void> {
  const scene = getScene(state);
  const ctx = getRenderingContext(state);
  const renderer = ctx.renderer;
  if (!scene || !renderer) {
    throw new Error(
      'VibeGame applyEquirectSkyEnvironment: scene or renderer not ready (run after configure/run).'
    );
  }

  const loader = new THREE.TextureLoader();
  const tex = await loader.loadAsync(url);
  tex.mapping = THREE.EquirectangularReflectionMapping;
  tex.colorSpace = THREE.SRGBColorSpace;

  const pmrem = new THREE.PMREMGenerator(renderer);
  pmrem.compileEquirectangularShader();
  const rt = pmrem.fromEquirectangular(tex);
  const envMap = rt.texture;
  tex.dispose();
  pmrem.dispose();
  scene.environment = envMap;

  if (options?.background !== false) {
    scene.background = envMap;
  }
}
