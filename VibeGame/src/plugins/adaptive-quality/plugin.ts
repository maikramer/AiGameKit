import type { Plugin } from '../../core';
import { AdaptiveQuality } from './components';
import { adaptiveQualityRecipe } from './recipes';
import {
  AdaptiveQualityApplySystem,
  AdaptiveQualityMeasureSystem,
} from './systems';

export const AdaptiveQualityPlugin: Plugin = {
  recipes: [adaptiveQualityRecipe],
  systems: [AdaptiveQualityMeasureSystem, AdaptiveQualityApplySystem],
  components: { 'adaptive-quality': AdaptiveQuality },
  config: {
    defaults: {
      'adaptive-quality': {
        enabled: 1,
        targetFps: 55,
        // Allow DPR < 1 so Low/Medium tiers can actually reclaim GPU budget.
        minPixelRatio: 0.75,
        maxPixelRatio: 1.5,
        currentTier: 0,
        emaFrameMs: 0,
        lastTransitionMs: 0,
        consecutiveHotFrames: 0,
        consecutiveColdFrames: 0,
        transitionCount: 0,
      },
    },
  },
};
