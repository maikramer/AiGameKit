import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'aigamekit-vibegame';
import {
  AdaptiveQuality,
  QualityTier,
  TIER_PRESETS,
  getAdaptiveQualityTier,
  isAdaptiveQualityActive,
} from '../../../src/plugins/adaptive-quality';

const PRESET_NUMERIC_KEYS = [
  'pixelRatioScale',
  'ssaoIntensityScale',
  'dofBokehScaleScale',
  'godRaysSamples',
  'pointShadowRefreshFrames',
] as const;

const PRESET_BOOL_KEYS = [
  'ssaoHalfResolution',
  'bloomMipmapBlur',
  'waterMirror',
] as const;

describe('QualityTier constants', () => {
  it('defines Max as 0', () => {
    expect(QualityTier.Max).toBe(0);
  });

  it('defines Low as 3', () => {
    expect(QualityTier.Low).toBe(3);
  });

  for (const [name, value] of Object.entries(QualityTier)) {
    it(`QualityTier.${name} is ${value}`, () => {
      expect(QualityTier[name as keyof typeof QualityTier]).toBe(value);
    });
  }
});

describe('TIER_PRESETS length and tier indices', () => {
  it('has exactly four presets (Max through Low)', () => {
    expect(TIER_PRESETS).toHaveLength(4);
  });

  for (let tier = 0; tier < 4; tier++) {
    it(`TIER_PRESETS[${tier}] is defined`, () => {
      expect(TIER_PRESETS[tier]).toBeDefined();
    });
  }
});

describe('TIER_PRESETS numeric fields', () => {
  for (let tier = 0; tier < 4; tier++) {
    for (const key of PRESET_NUMERIC_KEYS) {
      it(`tier ${tier} ${key} is finite`, () => {
        expect(Number.isFinite(TIER_PRESETS[tier][key])).toBe(true);
      });
    }
  }
});

describe('TIER_PRESETS boolean fields', () => {
  for (let tier = 0; tier < 4; tier++) {
    for (const key of PRESET_BOOL_KEYS) {
      it(`tier ${tier} ${key} is boolean`, () => {
        expect(typeof TIER_PRESETS[tier][key]).toBe('boolean');
      });
    }
  }
});

describe('TIER_PRESETS policy ordering', () => {
  it('Max tier has full pixel ratio scale', () => {
    expect(TIER_PRESETS[QualityTier.Max].pixelRatioScale).toBe(1.0);
  });

  it('Low tier has lowest pixel ratio scale', () => {
    expect(TIER_PRESETS[QualityTier.Low].pixelRatioScale).toBe(0.55);
  });

  for (let tier = 0; tier < 3; tier++) {
    it(`godRaysSamples non-increasing from tier ${tier} to ${tier + 1}`, () => {
      expect(TIER_PRESETS[tier + 1].godRaysSamples).toBeLessThanOrEqual(
        TIER_PRESETS[tier].godRaysSamples
      );
    });
  }

  for (let tier = 0; tier < 3; tier++) {
    it(`pixelRatioScale non-increasing from tier ${tier} to ${tier + 1}`, () => {
      expect(TIER_PRESETS[tier + 1].pixelRatioScale).toBeLessThanOrEqual(
        TIER_PRESETS[tier].pixelRatioScale
      );
    });
  }

  it('Low tier disables SSAO intensity', () => {
    expect(TIER_PRESETS[QualityTier.Low].ssaoIntensityScale).toBe(0);
  });

  it('Max tier enables water mirror', () => {
    expect(TIER_PRESETS[QualityTier.Max].waterMirror).toBe(true);
  });

  it('Medium tier disables water mirror', () => {
    expect(TIER_PRESETS[QualityTier.Medium].waterMirror).toBe(false);
  });
});

describe('getAdaptiveQualityTier', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.registerComponent('adaptive-quality', AdaptiveQuality);
  });

  it('returns Max when component is not registered on any entity', () => {
    expect(getAdaptiveQualityTier(state)).toBe(QualityTier.Max);
  });

  it('returns Max when entity exists but disabled', () => {
    const eid = state.createEntity();
    state.addComponent(eid, AdaptiveQuality);
    AdaptiveQuality.enabled[eid] = 0;
    AdaptiveQuality.currentTier[eid] = 2;
    expect(getAdaptiveQualityTier(state)).toBe(QualityTier.Max);
  });

  for (const tier of [0, 1, 2, 3]) {
    it(`reads currentTier ${tier} from enabled entity`, () => {
      const eid = state.createEntity();
      state.addComponent(eid, AdaptiveQuality);
      AdaptiveQuality.enabled[eid] = 1;
      AdaptiveQuality.currentTier[eid] = tier;
      expect(getAdaptiveQualityTier(state)).toBe(tier);
    });
  }
});

describe('isAdaptiveQualityActive', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.registerComponent('adaptive-quality', AdaptiveQuality);
  });

  it('is false when all entities with the component are disabled', () => {
    for (let i = 0; i < AdaptiveQuality.enabled.length; i++) {
      AdaptiveQuality.enabled[i] = 0;
    }
    expect(isAdaptiveQualityActive(state)).toBe(false);
  });

  it('is false when enabled flag is 0', () => {
    const eid = state.createEntity();
    state.addComponent(eid, AdaptiveQuality);
    AdaptiveQuality.enabled[eid] = 0;
    expect(isAdaptiveQualityActive(state)).toBe(false);
  });

  it('is true when enabled flag is 1', () => {
    const eid = state.createEntity();
    state.addComponent(eid, AdaptiveQuality);
    AdaptiveQuality.enabled[eid] = 1;
    expect(isAdaptiveQualityActive(state)).toBe(true);
  });
});
