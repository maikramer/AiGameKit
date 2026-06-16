import type { Plugin, Recipe } from '../../core';
import { PauseSystem } from './systems';

const pauseCoordinatorRecipe: Recipe = {
  name: 'PauseCoordinator',
  components: [],
};

export const PauseCoordinatorPlugin: Plugin = {
  systems: [PauseSystem],
  recipes: [pauseCoordinatorRecipe],
};
