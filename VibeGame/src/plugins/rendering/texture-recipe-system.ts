import * as THREE from 'three';
import { KTX2Loader } from 'three/examples/jsm/loaders/KTX2Loader.js';
import { defineQuery, type System } from '../../core';
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
    console.warn(
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
    const loader = new THREE.TextureLoader();

    for (const eid of textureRecipeQuery(state.world)) {
      if (TextureRecipe.pending[eid] === 0) continue;

      const url = textureUrls.get(eid);
      if (!url) {
        TextureRecipe.pending[eid] = 0;
        continue;
      }

      const loadTexture = async (texUrl: string): Promise<THREE.Texture> => {
        if (!isKTX2Url(texUrl)) return loader.loadAsync(texUrl);

        const { renderer } = getRenderingContext(state);
        if (!renderer) return loader.loadAsync(texUrl);

        const ktx2 = tryInitKTX2(renderer);
        if (!ktx2) return loader.loadAsync(texUrl);

        try {
          return await ktx2.loadAsync(texUrl);
        } catch {
          return loader.loadAsync(texUrl);
        }
      };

      void loadTexture(url)
        .then((texture) => {
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

          // Anisotropia — 8 is a safe minimum for all modern GPUs
          const maxAniso = 8;
          const aniso = TextureRecipe.anisotropy[eid];
          texture.anisotropy =
            aniso === 0 ? maxAniso : Math.min(aniso, maxAniso);

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
          console.error('[texture-recipe] Falha ao carregar textura:', err);
        })
        .finally(() => {
          TextureRecipe.pending[eid] = 0;
        });
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
        textureUrls.delete(eid);
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
