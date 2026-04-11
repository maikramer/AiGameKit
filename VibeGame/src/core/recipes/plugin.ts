import type { Plugin } from '../';
import { Parent } from './components';
import { entityRecipe, transformRecipe } from './recipes';

export const RecipePlugin: Plugin = {
  components: {
    parent: Parent,
  },
  recipes: [entityRecipe, transformRecipe],
};
