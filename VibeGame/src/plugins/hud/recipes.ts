import type { Recipe } from '../../core';

export const hudPanelRecipe: Recipe = {
  name: 'HudPanel',
  components: ['transform', 'hudPanel'],
  merge: true,
};

export const hudScreenLayerRecipe: Recipe = {
  name: 'HudScreenLayer',
  components: [],
};

export const hudWidgetRecipe: Recipe = {
  name: 'HudWidget',
  components: [],
  parserAttributes: ['type'],
  parserOwnsChildren: true,
};
