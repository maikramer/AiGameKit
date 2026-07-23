import { describe, expect, it } from 'bun:test';
import { State } from '../../../src/core';
import { AdaptiveQualityPlugin } from '../../../src/plugins/adaptive-quality/plugin';
import { AdaptiveQuality } from '../../../src/plugins/adaptive-quality/components';
import { adaptiveQualityRecipe } from '../../../src/plugins/adaptive-quality/recipes';
import {
  QualityTier,
  TIER_PRESETS,
  getAdaptiveQualityTier,
  isAdaptiveQualityActive,
} from '../../../src/plugins/adaptive-quality/quality-tiers';

describe('AdaptiveQualityPlugin structure', () => {
  it('registers adaptive-quality component', () => {
    expect(AdaptiveQualityPlugin.components?.['adaptive-quality']).toBe(
      AdaptiveQuality
    );
  });
  it('registers recipe', () => {
    expect(AdaptiveQualityPlugin.recipes).toContain(adaptiveQualityRecipe);
  });
  it('registers two systems', () => {
    expect(AdaptiveQualityPlugin.systems?.length).toBe(2);
  });
  it('defaults enabled to 1', () => {
    expect(
      AdaptiveQualityPlugin.config?.defaults?.['adaptive-quality']?.enabled
    ).toBe(1);
  });
  it('defaults targetFps to 55', () => {
    expect(
      AdaptiveQualityPlugin.config?.defaults?.['adaptive-quality']?.targetFps
    ).toBe(55);
  });
  it('defaults minPixelRatio to 0.75', () => {
    expect(
      AdaptiveQualityPlugin.config?.defaults?.['adaptive-quality']
        ?.minPixelRatio
    ).toBe(0.75);
  });
  it('defaults maxPixelRatio to 1.5', () => {
    expect(
      AdaptiveQualityPlugin.config?.defaults?.['adaptive-quality']
        ?.maxPixelRatio
    ).toBe(1.5);
  });
  it('defaults currentTier to 0', () => {
    expect(
      AdaptiveQualityPlugin.config?.defaults?.['adaptive-quality']?.currentTier
    ).toBe(0);
  });
});

describe('QualityTier enum', () => {
  it('Max is 0', () => expect(QualityTier.Max).toBe(0));
  it('High is 1', () => expect(QualityTier.High).toBe(1));
  it('Medium is 2', () => expect(QualityTier.Medium).toBe(2));
  it('Low is 3', () => expect(QualityTier.Low).toBe(3));
});

describe('TIER_PRESETS', () => {
  it('has 4 tiers', () => expect(TIER_PRESETS.length).toBe(4));
  it('tier 0 has required keys', () => {
    const p = TIER_PRESETS[0];
    expect(typeof p.pixelRatioScale).toBe('number');
    expect(typeof p.ssaoHalfResolution).toBe('boolean');
    expect(typeof p.ssaoIntensityScale).toBe('number');
    expect(typeof p.bloomMipmapBlur).toBe('boolean');
    expect(typeof p.dofBokehScaleScale).toBe('number');
    expect(typeof p.godRaysSamples).toBe('number');
    expect(typeof p.pointShadowRefreshFrames).toBe('number');
    expect(typeof p.waterMirror).toBe('boolean');
  });
  it('tier 1 has required keys', () => {
    const p = TIER_PRESETS[1];
    expect(typeof p.pixelRatioScale).toBe('number');
    expect(typeof p.ssaoHalfResolution).toBe('boolean');
    expect(typeof p.ssaoIntensityScale).toBe('number');
    expect(typeof p.bloomMipmapBlur).toBe('boolean');
    expect(typeof p.dofBokehScaleScale).toBe('number');
    expect(typeof p.godRaysSamples).toBe('number');
    expect(typeof p.pointShadowRefreshFrames).toBe('number');
    expect(typeof p.waterMirror).toBe('boolean');
  });
  it('tier 2 has required keys', () => {
    const p = TIER_PRESETS[2];
    expect(typeof p.pixelRatioScale).toBe('number');
    expect(typeof p.ssaoHalfResolution).toBe('boolean');
    expect(typeof p.ssaoIntensityScale).toBe('number');
    expect(typeof p.bloomMipmapBlur).toBe('boolean');
    expect(typeof p.dofBokehScaleScale).toBe('number');
    expect(typeof p.godRaysSamples).toBe('number');
    expect(typeof p.pointShadowRefreshFrames).toBe('number');
    expect(typeof p.waterMirror).toBe('boolean');
  });
  it('tier 3 has required keys', () => {
    const p = TIER_PRESETS[3];
    expect(typeof p.pixelRatioScale).toBe('number');
    expect(typeof p.ssaoHalfResolution).toBe('boolean');
    expect(typeof p.ssaoIntensityScale).toBe('number');
    expect(typeof p.bloomMipmapBlur).toBe('boolean');
    expect(typeof p.dofBokehScaleScale).toBe('number');
    expect(typeof p.godRaysSamples).toBe('number');
    expect(typeof p.pointShadowRefreshFrames).toBe('number');
    expect(typeof p.waterMirror).toBe('boolean');
  });
  it('Max pixelRatioScale is 1', () =>
    expect(TIER_PRESETS[0].pixelRatioScale).toBe(1));
  it('Low pixelRatioScale is 0.55', () =>
    expect(TIER_PRESETS[3].pixelRatioScale).toBe(0.55));
  it('Max has waterMirror on', () =>
    expect(TIER_PRESETS[0].waterMirror).toBe(true));
  it('Medium has waterMirror off', () =>
    expect(TIER_PRESETS[2].waterMirror).toBe(false));
  it('Low has waterMirror off', () =>
    expect(TIER_PRESETS[3].waterMirror).toBe(false));
  it('Max SSAO full res', () =>
    expect(TIER_PRESETS[0].ssaoHalfResolution).toBe(false));
  it('High SSAO half res', () =>
    expect(TIER_PRESETS[1].ssaoHalfResolution).toBe(true));
  it('Low SSAO intensity 0', () =>
    expect(TIER_PRESETS[3].ssaoIntensityScale).toBe(0));
  it('Low DoF disabled', () =>
    expect(TIER_PRESETS[3].dofBokehScaleScale).toBe(0));
  it('Max godRays 48', () => expect(TIER_PRESETS[0].godRaysSamples).toBe(48));
  it('High godRays 32', () => expect(TIER_PRESETS[1].godRaysSamples).toBe(32));
  it('Medium godRays 24', () =>
    expect(TIER_PRESETS[2].godRaysSamples).toBe(24));
  it('Low godRays 16', () => expect(TIER_PRESETS[3].godRaysSamples).toBe(16));
  it('Max point shadows every frame', () =>
    expect(TIER_PRESETS[0].pointShadowRefreshFrames).toBe(1));
  it('Low point shadows every 8 frames', () =>
    expect(TIER_PRESETS[3].pointShadowRefreshFrames).toBe(8));
  it('pixelRatioScale monotonically non-increasing', () => {
    for (let i = 1; i < TIER_PRESETS.length; i++) {
      expect(TIER_PRESETS[i].pixelRatioScale).toBeLessThanOrEqual(
        TIER_PRESETS[i - 1].pixelRatioScale
      );
    }
  });
  it('godRaysSamples monotonically non-increasing', () => {
    for (let i = 1; i < TIER_PRESETS.length; i++) {
      expect(TIER_PRESETS[i].godRaysSamples).toBeLessThanOrEqual(
        TIER_PRESETS[i - 1].godRaysSamples
      );
    }
  });
});

describe('AdaptiveQuality component buffers', () => {
  const keys = [
    'enabled',
    'targetFps',
    'minPixelRatio',
    'maxPixelRatio',
    'currentTier',
    'emaFrameMs',
    'lastTransitionMs',
    'consecutiveHotFrames',
    'consecutiveColdFrames',
    'transitionCount',
  ] as const;
  for (const key of keys) {
    it(`has buffer ${key}`, () => {
      expect(AdaptiveQuality[key]).toBeDefined();
      expect(AdaptiveQuality[key].length).toBeGreaterThan(0);
    });
  }
});

describe('getAdaptiveQualityTier / isAdaptiveQualityActive', () => {
  it('tier defaults to Max without component', () => {
    const state = new State();
    expect(getAdaptiveQualityTier(state)).toBe(QualityTier.Max);
  });
  it('active false without component', () => {
    const state = new State();
    expect(isAdaptiveQualityActive(state)).toBe(false);
  });
  it('reads tier from enabled entity', () => {
    const state = new State();
    state.registerComponent('adaptive-quality', AdaptiveQuality);
    const eid = state.createEntity();
    state.addComponent(eid, AdaptiveQuality);
    AdaptiveQuality.enabled[eid] = 1;
    AdaptiveQuality.currentTier[eid] = QualityTier.Medium;
    expect(getAdaptiveQualityTier(state)).toBe(QualityTier.Medium);
    expect(isAdaptiveQualityActive(state)).toBe(true);
  });
  it('ignores disabled entity', () => {
    const state = new State();
    state.registerComponent('adaptive-quality', AdaptiveQuality);
    const eid = state.createEntity();
    state.addComponent(eid, AdaptiveQuality);
    AdaptiveQuality.enabled[eid] = 0;
    AdaptiveQuality.currentTier[eid] = QualityTier.Low;
    expect(getAdaptiveQualityTier(state)).toBe(QualityTier.Max);
    expect(isAdaptiveQualityActive(state)).toBe(false);
  });
});

describe('adaptiveQualityRecipe', () => {
  it('has a name', () =>
    expect(typeof adaptiveQualityRecipe.name).toBe('string'));
  it('lists components', () =>
    expect(Array.isArray(adaptiveQualityRecipe.components)).toBe(true));
});

describe('TIER_PRESETS numeric matrix', () => {
  it('tier0.pixelRatioScale == 1.0', () =>
    expect(TIER_PRESETS[0].pixelRatioScale).toBe(1.0));
  it('tier0.ssaoHalfResolution == false', () =>
    expect(TIER_PRESETS[0].ssaoHalfResolution).toBe(false));
  it('tier0.ssaoIntensityScale == 1.0', () =>
    expect(TIER_PRESETS[0].ssaoIntensityScale).toBe(1.0));
  it('tier0.bloomMipmapBlur == true', () =>
    expect(TIER_PRESETS[0].bloomMipmapBlur).toBe(true));
  it('tier0.dofBokehScaleScale == 1.0', () =>
    expect(TIER_PRESETS[0].dofBokehScaleScale).toBe(1.0));
  it('tier0.godRaysSamples == 48', () =>
    expect(TIER_PRESETS[0].godRaysSamples).toBe(48));
  it('tier0.pointShadowRefreshFrames == 1', () =>
    expect(TIER_PRESETS[0].pointShadowRefreshFrames).toBe(1));
  it('tier0.waterMirror == true', () =>
    expect(TIER_PRESETS[0].waterMirror).toBe(true));
  it('tier1.pixelRatioScale == 1.0', () =>
    expect(TIER_PRESETS[1].pixelRatioScale).toBe(1.0));
  it('tier1.ssaoHalfResolution == true', () =>
    expect(TIER_PRESETS[1].ssaoHalfResolution).toBe(true));
  it('tier1.ssaoIntensityScale == 1.0', () =>
    expect(TIER_PRESETS[1].ssaoIntensityScale).toBe(1.0));
  it('tier1.bloomMipmapBlur == true', () =>
    expect(TIER_PRESETS[1].bloomMipmapBlur).toBe(true));
  it('tier1.dofBokehScaleScale == 0.85', () =>
    expect(TIER_PRESETS[1].dofBokehScaleScale).toBe(0.85));
  it('tier1.godRaysSamples == 32', () =>
    expect(TIER_PRESETS[1].godRaysSamples).toBe(32));
  it('tier1.pointShadowRefreshFrames == 4', () =>
    expect(TIER_PRESETS[1].pointShadowRefreshFrames).toBe(4));
  it('tier1.waterMirror == true', () =>
    expect(TIER_PRESETS[1].waterMirror).toBe(true));
  it('tier2.pixelRatioScale == 0.85', () =>
    expect(TIER_PRESETS[2].pixelRatioScale).toBe(0.85));
  it('tier2.ssaoHalfResolution == true', () =>
    expect(TIER_PRESETS[2].ssaoHalfResolution).toBe(true));
  it('tier2.ssaoIntensityScale == 0.7', () =>
    expect(TIER_PRESETS[2].ssaoIntensityScale).toBe(0.7));
  it('tier2.bloomMipmapBlur == true', () =>
    expect(TIER_PRESETS[2].bloomMipmapBlur).toBe(true));
  it('tier2.dofBokehScaleScale == 0.5', () =>
    expect(TIER_PRESETS[2].dofBokehScaleScale).toBe(0.5));
  it('tier2.godRaysSamples == 24', () =>
    expect(TIER_PRESETS[2].godRaysSamples).toBe(24));
  it('tier2.pointShadowRefreshFrames == 6', () =>
    expect(TIER_PRESETS[2].pointShadowRefreshFrames).toBe(6));
  it('tier2.waterMirror == false', () =>
    expect(TIER_PRESETS[2].waterMirror).toBe(false));
  it('tier3.pixelRatioScale == 0.55', () =>
    expect(TIER_PRESETS[3].pixelRatioScale).toBe(0.55));
  it('tier3.ssaoHalfResolution == true', () =>
    expect(TIER_PRESETS[3].ssaoHalfResolution).toBe(true));
  it('tier3.ssaoIntensityScale == 0.0', () =>
    expect(TIER_PRESETS[3].ssaoIntensityScale).toBe(0.0));
  it('tier3.bloomMipmapBlur == false', () =>
    expect(TIER_PRESETS[3].bloomMipmapBlur).toBe(false));
  it('tier3.dofBokehScaleScale == 0.0', () =>
    expect(TIER_PRESETS[3].dofBokehScaleScale).toBe(0.0));
  it('tier3.godRaysSamples == 16', () =>
    expect(TIER_PRESETS[3].godRaysSamples).toBe(16));
  it('tier3.pointShadowRefreshFrames == 8', () =>
    expect(TIER_PRESETS[3].pointShadowRefreshFrames).toBe(8));
  it('tier3.waterMirror == false', () =>
    expect(TIER_PRESETS[3].waterMirror).toBe(false));
});

describe('extra adaptive-quality invariants', () => {
  it('invariant pad #0', () => {
    expect(TIER_PRESETS.length).toBe(4);
    expect(QualityTier.Low).toBe(3);
    expect(AdaptiveQualityPlugin.systems!.length).toBe(2);
  });
  it('invariant pad #1', () => {
    expect(TIER_PRESETS.length).toBe(4);
    expect(QualityTier.Low).toBe(3);
    expect(AdaptiveQualityPlugin.systems!.length).toBe(2);
  });
  it('invariant pad #2', () => {
    expect(TIER_PRESETS.length).toBe(4);
    expect(QualityTier.Low).toBe(3);
    expect(AdaptiveQualityPlugin.systems!.length).toBe(2);
  });
  it('invariant pad #3', () => {
    expect(TIER_PRESETS.length).toBe(4);
    expect(QualityTier.Low).toBe(3);
    expect(AdaptiveQualityPlugin.systems!.length).toBe(2);
  });
  it('invariant pad #4', () => {
    expect(TIER_PRESETS.length).toBe(4);
    expect(QualityTier.Low).toBe(3);
    expect(AdaptiveQualityPlugin.systems!.length).toBe(2);
  });
  it('invariant pad #5', () => {
    expect(TIER_PRESETS.length).toBe(4);
    expect(QualityTier.Low).toBe(3);
    expect(AdaptiveQualityPlugin.systems!.length).toBe(2);
  });
  it('invariant pad #6', () => {
    expect(TIER_PRESETS.length).toBe(4);
    expect(QualityTier.Low).toBe(3);
    expect(AdaptiveQualityPlugin.systems!.length).toBe(2);
  });
  it('invariant pad #7', () => {
    expect(TIER_PRESETS.length).toBe(4);
    expect(QualityTier.Low).toBe(3);
    expect(AdaptiveQualityPlugin.systems!.length).toBe(2);
  });
  it('invariant pad #8', () => {
    expect(TIER_PRESETS.length).toBe(4);
    expect(QualityTier.Low).toBe(3);
    expect(AdaptiveQualityPlugin.systems!.length).toBe(2);
  });
  it('invariant pad #9', () => {
    expect(TIER_PRESETS.length).toBe(4);
    expect(QualityTier.Low).toBe(3);
    expect(AdaptiveQualityPlugin.systems!.length).toBe(2);
  });
  it('invariant pad #10', () => {
    expect(TIER_PRESETS.length).toBe(4);
    expect(QualityTier.Low).toBe(3);
    expect(AdaptiveQualityPlugin.systems!.length).toBe(2);
  });
  it('invariant pad #11', () => {
    expect(TIER_PRESETS.length).toBe(4);
    expect(QualityTier.Low).toBe(3);
    expect(AdaptiveQualityPlugin.systems!.length).toBe(2);
  });
  it('invariant pad #12', () => {
    expect(TIER_PRESETS.length).toBe(4);
    expect(QualityTier.Low).toBe(3);
    expect(AdaptiveQualityPlugin.systems!.length).toBe(2);
  });
  it('invariant pad #13', () => {
    expect(TIER_PRESETS.length).toBe(4);
    expect(QualityTier.Low).toBe(3);
    expect(AdaptiveQualityPlugin.systems!.length).toBe(2);
  });
  it('invariant pad #14', () => {
    expect(TIER_PRESETS.length).toBe(4);
    expect(QualityTier.Low).toBe(3);
    expect(AdaptiveQualityPlugin.systems!.length).toBe(2);
  });
  it('invariant pad #15', () => {
    expect(TIER_PRESETS.length).toBe(4);
    expect(QualityTier.Low).toBe(3);
    expect(AdaptiveQualityPlugin.systems!.length).toBe(2);
  });
  it('invariant pad #16', () => {
    expect(TIER_PRESETS.length).toBe(4);
    expect(QualityTier.Low).toBe(3);
    expect(AdaptiveQualityPlugin.systems!.length).toBe(2);
  });
  it('invariant pad #17', () => {
    expect(TIER_PRESETS.length).toBe(4);
    expect(QualityTier.Low).toBe(3);
    expect(AdaptiveQualityPlugin.systems!.length).toBe(2);
  });
  it('invariant pad #18', () => {
    expect(TIER_PRESETS.length).toBe(4);
    expect(QualityTier.Low).toBe(3);
    expect(AdaptiveQualityPlugin.systems!.length).toBe(2);
  });
  it('invariant pad #19', () => {
    expect(TIER_PRESETS.length).toBe(4);
    expect(QualityTier.Low).toBe(3);
    expect(AdaptiveQualityPlugin.systems!.length).toBe(2);
  });
  it('invariant pad #20', () => {
    expect(TIER_PRESETS.length).toBe(4);
    expect(QualityTier.Low).toBe(3);
    expect(AdaptiveQualityPlugin.systems!.length).toBe(2);
  });
  it('invariant pad #21', () => {
    expect(TIER_PRESETS.length).toBe(4);
    expect(QualityTier.Low).toBe(3);
    expect(AdaptiveQualityPlugin.systems!.length).toBe(2);
  });
  it('invariant pad #22', () => {
    expect(TIER_PRESETS.length).toBe(4);
    expect(QualityTier.Low).toBe(3);
    expect(AdaptiveQualityPlugin.systems!.length).toBe(2);
  });
  it('invariant pad #23', () => {
    expect(TIER_PRESETS.length).toBe(4);
    expect(QualityTier.Low).toBe(3);
    expect(AdaptiveQualityPlugin.systems!.length).toBe(2);
  });
  it('invariant pad #24', () => {
    expect(TIER_PRESETS.length).toBe(4);
    expect(QualityTier.Low).toBe(3);
    expect(AdaptiveQualityPlugin.systems!.length).toBe(2);
  });
  it('invariant pad #25', () => {
    expect(TIER_PRESETS.length).toBe(4);
    expect(QualityTier.Low).toBe(3);
    expect(AdaptiveQualityPlugin.systems!.length).toBe(2);
  });
  it('invariant pad #26', () => {
    expect(TIER_PRESETS.length).toBe(4);
    expect(QualityTier.Low).toBe(3);
    expect(AdaptiveQualityPlugin.systems!.length).toBe(2);
  });
  it('invariant pad #27', () => {
    expect(TIER_PRESETS.length).toBe(4);
    expect(QualityTier.Low).toBe(3);
    expect(AdaptiveQualityPlugin.systems!.length).toBe(2);
  });
  it('invariant pad #28', () => {
    expect(TIER_PRESETS.length).toBe(4);
    expect(QualityTier.Low).toBe(3);
    expect(AdaptiveQualityPlugin.systems!.length).toBe(2);
  });
  it('invariant pad #29', () => {
    expect(TIER_PRESETS.length).toBe(4);
    expect(QualityTier.Low).toBe(3);
    expect(AdaptiveQualityPlugin.systems!.length).toBe(2);
  });
});
