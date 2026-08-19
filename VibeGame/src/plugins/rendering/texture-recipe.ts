import { defineComponent, F32, U8 } from '../../core/ecs/component-storage';

/**
 * Referência a uma textura gerada pelo Texture2D (pipeline Python).
 * Carrega um PNG/JPG e associa ao material de uma entidade Renderer ou Terrain.
 *
 * Presets disponíveis no Texture2D: Wood, Fabric, Metal, Stone, Brick, Leather, Concrete, etc.
 */
export const TextureRecipe = defineComponent({
  pending: U8,
  repeatMode: U8,
  repeatX: F32,
  repeatY: F32,
  flipX: U8,
  flipY: U8,
  anisotropy: U8,
  channel: U8,
});

export const TextureRecipeLoaded = defineComponent({
  ready: U8,
});
