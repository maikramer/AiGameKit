import { beforeEach, describe, expect, it } from 'bun:test';
import { State, defineQuery } from 'aigamekit-vibegame';
import {
  AdaptiveQuality,
  AdaptiveQualityPlugin,
} from '../../../src/plugins/adaptive-quality';
import { adaptiveQualityRecipe } from '../../../src/plugins/adaptive-quality/recipes';
import {
  AdaptiveQualityApplySystem,
  AdaptiveQualityMeasureSystem,
} from '../../../src/plugins/adaptive-quality/systems';

const ADAPTIVE_FIELDS = [
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

describe('AdaptiveQuality component buffers', () => {
  let eid: number;

  beforeEach(() => {
    eid = 1;
    // o índice 1 dos buffers singleton é partilhado com outros ficheiros do
    // worker — repõe os defaults antes de cada asserção
    for (const field of ADAPTIVE_FIELDS) AdaptiveQuality[field][eid] = 0;
  });

  for (const field of ADAPTIVE_FIELDS) {
    it(`${field} TypedArray is defined`, () => {
      expect(AdaptiveQuality[field]).toBeDefined();
      expect(AdaptiveQuality[field].length).toBeGreaterThan(eid);
    });
  }

  it('enabled defaults to 0 for fresh index', () => {
    expect(AdaptiveQuality.enabled[eid]).toBe(0);
  });

  it('supports query via defineQuery', () => {
    const state = new State();
    state.registerComponent('adaptive-quality', AdaptiveQuality);
    const entity = state.createEntity();
    state.addComponent(entity, AdaptiveQuality);
    const q = defineQuery([AdaptiveQuality])(state.world);
    expect(q).toContain(entity);
  });
});

describe('adaptiveQualityRecipe', () => {
  it('name is AdaptiveQuality', () => {
    expect(adaptiveQualityRecipe.name).toBe('AdaptiveQuality');
  });

  it('requires transform and adaptive-quality', () => {
    expect(adaptiveQualityRecipe.components).toEqual([
      'transform',
      'adaptive-quality',
    ]);
  });
});

describe('AdaptiveQualityPlugin', () => {
  it('registers adaptive-quality component', () => {
    expect(AdaptiveQualityPlugin.components?.['adaptive-quality']).toBe(
      AdaptiveQuality
    );
  });

  it('includes adaptive quality recipe', () => {
    expect(AdaptiveQualityPlugin.recipes).toContain(adaptiveQualityRecipe);
  });

  it('registers measure and apply systems in order', () => {
    expect(AdaptiveQualityPlugin.systems?.[0]).toBe(
      AdaptiveQualityMeasureSystem
    );
    expect(AdaptiveQualityPlugin.systems?.[1]).toBe(AdaptiveQualityApplySystem);
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

  for (const field of ADAPTIVE_FIELDS) {
    it(`plugin defaults include ${field}`, () => {
      const defaults =
        AdaptiveQualityPlugin.config?.defaults?.['adaptive-quality'];
      expect(defaults).toBeDefined();
      expect(Object.prototype.hasOwnProperty.call(defaults, field)).toBe(true);
    });
  }
});

describe('AdaptiveQuality systems metadata', () => {
  it('measure system runs in late group', () => {
    expect(AdaptiveQualityMeasureSystem.group).toBe('late');
  });

  it('apply system runs in draw group', () => {
    expect(AdaptiveQualityApplySystem.group).toBe('draw');
  });

  it('measure system has update', () => {
    expect(typeof AdaptiveQualityMeasureSystem.update).toBe('function');
  });

  it('apply system has update', () => {
    expect(typeof AdaptiveQualityApplySystem.update).toBe('function');
  });
});
