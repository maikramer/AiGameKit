import type { Plugin } from '../../core';
import { TweenData } from './components';
import { tweenParser } from './parser';
import { tweenRecipe } from './recipe';
import { TweenProcessingSystem } from './systems';

export const TweeningPlugin: Plugin = {
  recipes: [tweenRecipe],
  systems: [TweenProcessingSystem],
  components: {
    'tween-data': TweenData,
  },
  config: {
    parsers: {
      Tween: tweenParser,
    },
  },
};
