import * as THREE from 'three';
import { defineQuery, type System } from '../../core';
import { TextureRecipe } from './texture-recipe';

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

      void loader
        .loadAsync(url)
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

          // Anisotropia
          const maxAniso = 1; // conservative default; GPU value requires renderer ref
          const aniso = TextureRecipe.anisotropy[eid];
          if (aniso > 0 && aniso <= maxAniso) {
            texture.anisotropy = aniso;
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
