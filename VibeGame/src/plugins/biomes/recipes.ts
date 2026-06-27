import type { Recipe } from '../../core';

/**
 * `<BiomeRegion>` — declares one polygonal biome overlay. All listed attributes
 * are consumed by {@link biomeRegionParser} (the engine does not auto-apply
 * them), so they appear in `parserAttributes`.
 */
export const biomeRegionRecipe: Recipe = {
  name: 'BiomeRegion',
  components: ['transform', 'biome-region'],
  parserAttributes: [
    'id',
    'type',
    'polygon',
    'tint',
    'fog-color',
    'fog-density',
    'ambient',
    'bgm-layer',
    'terrain-texture',
  ],
  parserOwnsChildren: false,
};
