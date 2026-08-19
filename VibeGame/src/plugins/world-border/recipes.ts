import type { Recipe } from '../../core';

/** `<WorldBorder radius="600" warn-seconds="5" margin="24">` — soft circular
 *  border: countdown warning, then teleport back inside. */
export const worldBorderRecipe: Recipe = {
  name: 'WorldBorder',
  components: ['world-border'],
  parserAttributes: ['radius', 'warn-seconds', 'margin'],
};
