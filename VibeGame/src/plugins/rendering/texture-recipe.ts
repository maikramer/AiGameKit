import { MAX_ENTITIES } from '../../core/ecs/constants';

/**
 * Referência a uma textura gerada pelo Texture2D (pipeline Python).
 * Carrega um PNG/JPG e associa ao material de uma entidade Renderer ou Terrain.
 *
 * Presets disponíveis no Texture2D: Wood, Fabric, Metal, Stone, Brick, Leather, Concrete, etc.
 */
export const TextureRecipe = {
  pending: new Uint8Array(MAX_ENTITIES),
  repeatMode: new Uint8Array(MAX_ENTITIES),
  repeatX: new Float32Array(MAX_ENTITIES),
  repeatY: new Float32Array(MAX_ENTITIES),
  flipX: new Uint8Array(MAX_ENTITIES),
  flipY: new Uint8Array(MAX_ENTITIES),
  anisotropy: new Uint8Array(MAX_ENTITIES),
  channel: new Uint8Array(MAX_ENTITIES),
} as const;

export const TextureRecipeLoaded = {
  ready: new Uint8Array(MAX_ENTITIES),
} as const;
