import { defineComponent, Types } from 'bitecs';

/**
 * Referência a uma textura gerada pelo Texture2D (pipeline Python).
 * Carrega um PNG/JPG e associa ao material de uma entidade Renderer ou Terrain.
 *
 * Presets disponíveis no Texture2D: Wood, Fabric, Metal, Stone, Brick, Leather, Concrete, etc.
 */
export const TextureRecipe = defineComponent({
  /** URL da textura (PNG/JPG gerado pelo Texture2D). */
  url: Types.eid, // placeholder — URL real fica no mapa
  /** 1 = carregamento pendente. */
  pending: Types.ui8,
  /** Modo de repeat: 0 = ClampToEdge, 1 = RepeatWrapping. */
  repeatMode: Types.ui8,
  /** Quantas vezes a textura repete em X. */
  repeatX: Types.f32,
  /** Quantas vezes a textura repete em Y. */
  repeatY: Types.f32,
  /** Inverter horizontalmente. */
  flipX: Types.ui8,
  /** Inverter verticalmente. */
  flipY: Types.ui8,
  /** Anisotropia (0 = usar default da GPU). */
  anisotropy: Types.ui8,
  /** Canal de destino: 0 = map (diffuse), 1 = normalMap, 2 = roughnessMap, 3 = metalnessMap, 4 = aoMap. */
  channel: Types.ui8,
});

export const TextureRecipeLoaded = defineComponent({
  /** 1 = textura carregada e pronta. */
  ready: Types.ui8,
});
