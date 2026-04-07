/**
 * Skymap2D / equirect PNG → ``Scene.environment`` (PMREM) para PBR no browser.
 *
 * Three.js equirectUv: u = atan(dir.z, dir.x), v = asin(dir.y).
 * Panoramas gerados (Flux-LoRA-Equirectangular e semelhantes) usam a convenção padrão:
 *   pixel-x = longitude (azimute 360°), pixel-y = latitude (elevação).
 * O Three.js assume exatamente essa convenção, portanto **não** é necessário rodar ou transpor
 * o bitmap — basta garantir que a textura é 2:1 (paisagem) e usar EquirectangularReflectionMapping.
 */
/* global fetch */
import * as THREE from 'three';

import type { State } from '../core';
import { getRenderingContext, getScene } from '../plugins/rendering';

export interface EquirectSkyOptions {
  /** Se true (defeito), também define ``scene.background``; se false, só iluminação IBL. */
  background?: boolean;
  /**
   * Rotação horizontal do panorama em graus (0–360). Roda o bitmap antes do PMREM para
   * alinhar a direcção "frente" da câmara com o centro da imagem. Defeito: 0.
   */
  rotationDeg?: number;
}

/**
 * Aplica rotação horizontal (pixel-shift em U) ao bitmap via canvas, para que o PMREM
 * (cujo shader interno ignora ``texture.offset``) receba a textura já alinhada.
 * Retorna a textura original se o deslocamento for 0.
 */
function rotateEquirectBitmap(
  tex: THREE.Texture,
  degrees: number
): THREE.Texture {
  const shift = ((degrees % 360) + 360) % 360;
  if (shift === 0) return tex;
  const img = tex.image as HTMLImageElement | undefined;
  if (!img || !img.width) return tex;
  if (typeof document === 'undefined') return tex;

  const w = img.width;
  const h = img.height;
  const sx = Math.round((shift / 360) * w) % w;
  if (sx === 0) return tex;

  const canvas = document.createElement('canvas');
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext('2d')!;
  ctx.drawImage(img, sx, 0, w - sx, h, 0, 0, w - sx, h);
  ctx.drawImage(img, 0, 0, sx, h, w - sx, 0, sx, h);

  const out = new THREE.CanvasTexture(canvas);
  out.mapping = tex.mapping;
  out.colorSpace = tex.colorSpace;
  out.needsUpdate = true;
  tex.dispose();
  return out;
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
  const loaded = await loader.loadAsync(url);
  loaded.mapping = THREE.EquirectangularReflectionMapping;
  loaded.colorSpace = THREE.SRGBColorSpace;

  const img = loaded.image as HTMLImageElement | undefined;
  if (img && img.width && img.height) {
    const ratio = img.width / img.height;
    if (Math.abs(ratio - 2.0) > 0.15) {
      console.warn(
        `[VibeGame] Sky texture "${url}" has aspect ratio ${ratio.toFixed(2)}:1 (expected 2:1 for equirectangular). ` +
          'The sky may look distorted. Generate with 2:1 ratio (e.g. 2048×1024) for correct results.'
      );
    }
  }

  const tex = rotateEquirectBitmap(loaded, options?.rotationDeg ?? 0);

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
      for (const name of ['sky', 'environment', 'skybox', 'equirect']) {
        const url = `${dir}${name}${ext}`;
        try {
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
