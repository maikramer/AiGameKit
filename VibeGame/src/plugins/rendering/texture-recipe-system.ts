import { logger } from '../../core/utils/logger';
import * as THREE from 'three';
import { KTX2Loader } from 'three/examples/jsm/loaders/KTX2Loader.js';
import { defineQuery, type State, type System } from '../../core';
import { TextureRecipe } from './texture-recipe';
import { getRenderingContext } from './utils';

function isKTX2Url(url: string): boolean {
  return url.endsWith('.ktx2') || url.endsWith('.basis');
}

let _ktx2Loader: KTX2Loader | null | undefined = undefined;

function tryInitKTX2(renderer: THREE.WebGLRenderer): KTX2Loader | null {
  if (_ktx2Loader !== undefined) return _ktx2Loader;
  try {
    _ktx2Loader = new KTX2Loader()
      .setTranscoderPath(
        `https://unpkg.com/three@0.${THREE.REVISION}.0/examples/jsm/libs/basis/`
      )
      .detectSupport(renderer);
    return _ktx2Loader;
  } catch (e) {
    logger.warn(
      '[texture-recipe] KTX2Loader init failed — KTX2 textures disabled.',
      e
    );
    _ktx2Loader = null;
    return null;
  }
}

// Contexto: entity → URL de textura
const textureUrls = new Map<number, string>();
// entity → THREE.Texture carregada
const textureAssets = new Map<number, THREE.Texture>();
// Cache de texturas invertidas (smoothness → roughness)
const invertedCache = new Map<number, THREE.CanvasTexture>();

/**
 * Decode cache keyed by URL: the decoded image (PNG/JPG/KTX2) is shared across
 * every entity that references the same URL, so the GPU upload + image decode
 * happens once. Each consumer clones the decoded texture so it can apply its
 * own wrap/repeat/anisotropy/colorSpace without affecting siblings — `clone()`
 * shares the underlying `image` (one GPU texture), only duplicating metadata.
 */
const decodedTextureByUrl = new Map<string, Promise<THREE.Texture>>();

/** Ref-counts decoded textures so they are freed when the last consumer drops. */
const decodedTextureRefcount = new Map<string, number>();

function rememberDecodedTexture(url: string): Promise<THREE.Texture> {
  const existing = decodedTextureByUrl.get(url);
  if (existing) {
    decodedTextureRefcount.set(url, (decodedTextureRefcount.get(url) ?? 0) + 1);
    return existing;
  }
  // The first consumer drives the decode; later consumers await the same promise.
  const promise = Promise.resolve().then(async () => {
    const loader = new THREE.TextureLoader();
    if (!isKTX2Url(url)) return loader.loadAsync(url);
    const renderer = getRendererForTextureLoad();
    if (!renderer) return loader.loadAsync(url);
    const ktx2 = tryInitKTX2(renderer);
    if (!ktx2) return loader.loadAsync(url);
    try {
      return await ktx2.loadAsync(url);
    } catch {
      return loader.loadAsync(url);
    }
  });
  decodedTextureByUrl.set(url, promise);
  decodedTextureRefcount.set(url, 1);
  return promise;
}

function releaseDecodedTexture(url: string): void {
  const rc = decodedTextureRefcount.get(url);
  if (rc === undefined) return;
  if (rc > 1) {
    decodedTextureRefcount.set(url, rc - 1);
    return;
  }
  // Last consumer gone: drop the cached decode so the image/GPU texture can be
  // freed. We can't synchronously dispose the source here because clones may
  // still reference the image — clones own their own GPU upload, so disposing
  // the master only invalidates the master's GPU handle. Resolve + dispose.
  decodedTextureRefcount.delete(url);
  const promise = decodedTextureByUrl.get(url);
  decodedTextureByUrl.delete(url);
  if (promise) {
    void promise.then((tex) => {
      try {
        tex.dispose();
      } catch {
        // Already disposed.
      }
    });
  }
}

// Lazily-resolved renderer accessor. TextureRecipeLoadSystem runs in the
// `setup` group; the renderer may not exist yet on the very first tick, so we
// resolve it at load time (inside rememberDecodedTexture) rather than capture.
let getRendererForTextureLoad: () => THREE.WebGLRenderer | null = () => null;

// Materialize outputs smoothness maps; Three.js roughnessMap expects roughness (inverse).
// Auto-detected by filename containing "smoothness".
function invertSmoothnessTexture(
  sourceTexture: THREE.Texture
): THREE.CanvasTexture {
  const img = sourceTexture.image as HTMLImageElement;
  const canvas = document.createElement('canvas');
  canvas.width = img.width;
  canvas.height = img.height;
  const ctx = canvas.getContext('2d')!;
  ctx.drawImage(img, 0, 0);
  const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
  const data = imageData.data;
  for (let i = 0; i < data.length; i += 4) {
    data[i] = 255 - data[i]; // invert R channel
  }
  ctx.putImageData(imageData, 0, 0);
  const inverted = new THREE.CanvasTexture(canvas);
  inverted.colorSpace = THREE.LinearSRGBColorSpace;
  return inverted;
}

/** Associa uma URL de textura Texture2D a uma entidade. */
export function setTextureRecipeUrl(eid: number, url: string): void {
  textureUrls.set(eid, url);
  TextureRecipe.pending[eid] = 1;
}

/** Retorna a textura Three.js carregada para uma entidade. */
export function getTextureAsset(eid: number): THREE.Texture | undefined {
  return textureAssets.get(eid);
}

const textureRecipeQuery = defineQuery([TextureRecipe]);

const CHANNEL_MAP = [
  'map', // 0 — albedo/diffuse
  'normalMap', // 1
  'roughnessMap', // 2
  'metalnessMap', // 3
  'aoMap', // 4 — ambient occlusion
  'displacementMap', // 5
] as const;

export const TextureRecipeLoadSystem: System = {
  group: 'setup',
  update: (state) => {
    // Resolve the renderer lazily so the decode cache can pick KTX2 when the
    // GPU is ready. Captured fresh each tick (cheap; WeakMap lookup).
    const { renderer } = getRenderingContext(state);
    getRendererForTextureLoad = () => renderer ?? null;

    for (const eid of textureRecipeQuery(state.world)) {
      if (TextureRecipe.pending[eid] === 0) continue;

      const url = textureUrls.get(eid);
      if (!url) {
        TextureRecipe.pending[eid] = 0;
        continue;
      }

      void rememberDecodedTexture(url)
        .then((decoded) => {
          // Clone so this entity can apply its own wrap/repeat/anisotropy/
          // colorSpace without affecting siblings sharing the same decode.
          // `clone()` shares the underlying `image` → one GPU upload per URL.
          const texture = decoded.clone();
          texture.needsUpdate = true;

          // Configura wrapping
          const repeatX = TextureRecipe.repeatX[eid] || 1;
          const repeatY = TextureRecipe.repeatY[eid] || 1;
          const useRepeat = TextureRecipe.repeatMode[eid] === 1;

          texture.wrapS = useRepeat
            ? THREE.RepeatWrapping
            : THREE.ClampToEdgeWrapping;
          texture.wrapT = useRepeat
            ? THREE.RepeatWrapping
            : THREE.ClampToEdgeWrapping;
          texture.repeat.set(repeatX, repeatY);

          // flipX not available on THREE.Texture; skip
          if (TextureRecipe.flipY[eid]) texture.flipY = true;

          // Anisotropy — use the hardware maximum (typically 16 on desktop).
          // Sharper grazing-angle textures (terrain/floor) at ~zero cost.
          const aniso = TextureRecipe.anisotropy[eid];
          if (aniso > 0) {
            texture.anisotropy = aniso;
          } else if (renderer) {
            texture.anisotropy = renderer.capabilities.getMaxAnisotropy();
          } else {
            texture.anisotropy = 8;
          }

          const channel = TextureRecipe.channel[eid] || 0;
          texture.colorSpace =
            channel === 0 ? THREE.SRGBColorSpace : THREE.LinearSRGBColorSpace;
          texture.needsUpdate = true;

          const isSmoothness =
            channel === 2 && url.toLowerCase().includes('smoothness');
          if (isSmoothness) {
            const inverted = invertSmoothnessTexture(texture);
            texture.dispose();
            invertedCache.set(eid, inverted);
            textureAssets.set(eid, inverted);
          } else {
            textureAssets.set(eid, texture);
          }
        })
        .catch((err: unknown) => {
          logger.error('[texture-recipe] Falha ao carregar textura:', err);
          // Release the ref we took on failure so the cache slot isn't leaked.
          releaseDecodedTexture(url);
        })
        .finally(() => {
          TextureRecipe.pending[eid] = 0;
        });
    }
  },
  dispose(_state: State) {
    for (const texture of textureAssets.values()) {
      try {
        texture.dispose();
      } catch {
        // Texture may already be disposed.
      }
    }
    textureAssets.clear();
    for (const texture of invertedCache.values()) {
      try {
        texture.dispose();
      } catch {
        // Texture may already be disposed.
      }
    }
    invertedCache.clear();
    // Drop decode-cache refs for every URL we were tracking.
    for (const url of textureUrls.values()) {
      releaseDecodedTexture(url);
    }
    textureUrls.clear();
    // KTX2Loader.dispose() terminates its worker pool.
    if (_ktx2Loader && typeof _ktx2Loader.dispose === 'function') {
      try {
        _ktx2Loader.dispose();
      } catch {
        // Worker pool may already be gone.
      }
      _ktx2Loader = undefined;
    }
  },
};

export const TextureRecipeCleanupSystem: System = {
  group: 'draw',
  update: (state) => {
    for (const [eid, texture] of textureAssets) {
      if (!state.exists(eid) || !state.hasComponent(eid, TextureRecipe)) {
        texture.dispose();
        textureAssets.delete(eid);
        const url = textureUrls.get(eid);
        textureUrls.delete(eid);
        if (url) releaseDecodedTexture(url);
        const inverted = invertedCache.get(eid);
        if (inverted) {
          inverted.dispose();
          invertedCache.delete(eid);
        }
      }
    }
  },
};

/**
 * Aplica a textura carregada ao material de um mesh Three.js.
 * Retorna true se aplicou com sucesso.
 */
export function applyTextureToMaterial(
  eid: number,
  material: THREE.Material
): boolean {
  const texture = textureAssets.get(eid);
  if (!texture) return false;

  const channel = TextureRecipe.channel[eid] || 0;
  const key = CHANNEL_MAP[channel];
  if (key && key in material) {
    (material as unknown as Record<string, THREE.Texture | null>)[key] =
      texture;
    material.needsUpdate = true;
    return true;
  }
  return false;
}
