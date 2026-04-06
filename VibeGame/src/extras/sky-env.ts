/**
 * Skymap2D / equirect PNG → ``Scene.environment`` (PMREM) para PBR no browser.
 */
/* global fetch */
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

/** Common sky search paths (relative to site root). */
const SKY_SEARCH_PATHS = [
  '/assets/sky/',
  '/assets/skymaps/',
  '/assets/environment/',
  '/public/assets/sky/',
];

/** Extensions recognized as equirectangular sky textures. */
const SKY_EXTENSIONS = ['.png', '.jpg', '.jpeg', '.webp', '.hdr', '.exr'];

/**
 * Attempt to auto-discover and load a sky environment texture.
 *
 * Tries common paths (``/assets/sky/``, etc.) for PNG/JPG/HDR files.
 * Returns ``true`` if a sky was found and applied, ``false`` otherwise.
 *
 * @param state - VibeGame state (after runtime started with renderer).
 * @param basePath - Optional custom search directory (e.g. ``/assets/my_sky/``).
 */
export async function autoLoadSkyEnvironment(
  state: State,
  options?: EquirectSkyOptions & { basePath?: string }
): Promise<boolean> {
  const searchPaths = options?.basePath ? [options.basePath] : SKY_SEARCH_PATHS;

  for (const dir of searchPaths) {
    for (const ext of SKY_EXTENSIONS) {
      // Try common sky filenames
      for (const name of ['sky', 'environment', 'skybox', 'equirect']) {
        const url = `${dir}${name}${ext}`;
        try {
          // Use fetch to check existence (HEAD request is lighter but may not
          // work on all static hosts; GET with Range avoids downloading the file)
          const resp = await fetch(url, { method: 'HEAD' });
          if (resp.ok) {
            await applyEquirectSkyEnvironment(state, url, options);
            return true;
          }
        } catch {
          // Not found, try next
        }
      }
    }
  }
  return false;
}
