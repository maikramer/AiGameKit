import type { Plugin } from '../../core';
import { CompositionPending } from './components';
import { compositionParser } from './parser';
import { compositionRecipe } from './recipes';
import {
  CompositionColliderSystem,
  CompositionSetupSystem,
  CompositionSyncSystem,
} from './systems';

export const CompositionPlugin: Plugin = {
  recipes: [compositionRecipe],
  systems: [
    CompositionSetupSystem,
    CompositionColliderSystem,
    CompositionSyncSystem,
  ],
  components: {
    compositionPending: CompositionPending,
  },
  config: {
    parsers: {
      Composition: compositionParser,
    },
  },
};
