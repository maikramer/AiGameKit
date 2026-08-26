import { describe, expect, it } from 'bun:test';
import {
  QUALITY_MODE_VALUES,
  parseQualityQuery,
  setQualityMode,
  getQualityMode,
  getAdaptiveQualityTier,
} from '../../../src/plugins/adaptive-quality/quality-tiers';
import { AdaptiveQuality } from '../../../src/plugins/adaptive-quality/components';
import type { State } from '../../../src/core';

function makeState(): State {
  return {
    world: 0,
    getComponent: (name: string) =>
      name === 'adaptive-quality' ? AdaptiveQuality : undefined,
  } as unknown as State;
}

describe('parseQualityQuery', () => {
  it('lê ?quality= válido (todas as modalidades)', () => {
    expect(parseQualityQuery('http://x/?quality=low')).toBe('low');
    expect(parseQualityQuery('http://x/?quality=medium')).toBe('medium');
    expect(parseQualityQuery('http://x/?quality=high')).toBe('high');
    expect(parseQualityQuery('http://x/?quality=max')).toBe('max');
    expect(parseQualityQuery('http://x/?quality=auto')).toBe('auto');
  });

  it('aceita o alias aq= e é case-insensitive', () => {
    expect(parseQualityQuery('http://x/?aq=LOW')).toBe('low');
    expect(parseQualityQuery('http://x/?foo=1&aq=medium')).toBe('medium');
  });

  it('ignora valores inválidos e URLs sem query', () => {
    expect(parseQualityQuery('http://x/?quality=ultra')).toBeNull();
    expect(parseQualityQuery('http://x/?quality=')).toBeNull();
    expect(parseQualityQuery('http://x/')).toBeNull();
  });
});

describe('setQualityMode / getQualityMode', () => {
  it('força o tier correspondente e volta ao auto', () => {
    const state = makeState();
    AdaptiveQuality.enabled[0] = 1;
    AdaptiveQuality.mode[0] = 0;
    AdaptiveQuality.currentTier[0] = 2;

    expect(setQualityMode(state, 'low')).toBe(true);
    expect(getQualityMode(state)).toBe('low');
    expect(getAdaptiveQualityTier(state)).toBe(3);

    expect(setQualityMode(state, 'max')).toBe(true);
    expect(getAdaptiveQualityTier(state)).toBe(0);

    expect(setQualityMode(state, 'auto')).toBe(true);
    // Auto retoma o tier que o scaler tinha aplicado.
    expect(getAdaptiveQualityTier(state)).toBe(2);
  });

  it('retorna false sem entidade ativa', () => {
    const state = makeState();
    AdaptiveQuality.enabled[0] = 0;
    expect(setQualityMode(state, 'high')).toBe(false);
  });

  it('mapeamento modo→valor é contíguo e coerente', () => {
    expect(QUALITY_MODE_VALUES.auto).toBe(0);
    expect(QUALITY_MODE_VALUES.low).toBe(1);
    expect(QUALITY_MODE_VALUES.max).toBe(4);
  });
});
