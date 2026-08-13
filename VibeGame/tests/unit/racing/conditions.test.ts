import { afterEach, describe, expect, it } from 'bun:test';
import {
  beginRaceFromQualifying,
  conditionGripMul,
  conditionIsNight,
  conditionWetness,
  getRaceState,
  resetRaceState,
  setRaceState,
} from '../../../src/plugins/racing/race-state';

afterEach(() => {
  resetRaceState();
});

describe('track conditions', () => {
  it('maps wet and storm onto full wetness, night onto headlights', () => {
    expect(conditionWetness('dry')).toBe(0);
    expect(conditionWetness('night')).toBe(0);
    expect(conditionWetness('wet')).toBe(1);
    expect(conditionWetness('storm')).toBe(1);
    expect(conditionIsNight('night')).toBe(true);
    expect(conditionIsNight('storm')).toBe(true);
    expect(conditionIsNight('wet')).toBe(false);
    expect(conditionGripMul('wet')).toBeCloseTo(0.58, 5);
    expect(conditionGripMul('dry')).toBe(1);
  });
});

describe('beginRaceFromQualifying', () => {
  it('only promotes a finished qualifying session', () => {
    expect(beginRaceFromQualifying()).toBe(false);
    setRaceState({ session: 'qualifying', phase: 'finished' });
    expect(beginRaceFromQualifying()).toBe(true);
    expect(getRaceState().session).toBe('race');
    expect(getRaceState().phase).toBe('grid');
  });
});
