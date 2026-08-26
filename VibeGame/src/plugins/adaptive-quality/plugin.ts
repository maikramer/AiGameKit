import type { Plugin, State } from '../../core';
import { AdaptiveQuality } from './components';
import { adaptiveQualityRecipe } from './recipes';
import { captureQualityQueryOverride } from './quality-tiers';
import {
  AdaptiveQualityApplySystem,
  AdaptiveQualityMeasureSystem,
} from './systems';

export const AdaptiveQualityPlugin: Plugin = {
  recipes: [adaptiveQualityRecipe],
  systems: [AdaptiveQualityMeasureSystem, AdaptiveQualityApplySystem],
  components: { 'adaptive-quality': AdaptiveQuality },
  initialize(_state: State) {
    // The world XML (and its <AdaptiveQuality> entity) is parsed after plugin
    // init, so the query override is captured here and applied by the measure
    // system on its first update — `?quality=low|medium|high|max|auto`.
    captureQualityQueryOverride();
  },
  config: {
    defaults: {
      'adaptive-quality': {
        enabled: 1,
        // Auto is the default: the auto-scaler drives the tier. Force
        // Low/Medium/High/Max via the options UI, the `mode` XML attribute or
        // the `?quality=` query to pin a tier for testing.
        mode: 0,
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
    enums: {
      'adaptive-quality': {
        mode: { auto: 0, low: 1, medium: 2, high: 3, max: 4 },
      },
    },
  },
};
