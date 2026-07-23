import { describe, expect, it } from 'bun:test';
import { MAX_ENTITIES } from '../../../src/core/ecs/constants';
import { Destructible } from '../../../src/plugins/destructible/components';

const UINT8_FIELDS = [
  'hits',
  'hitsTaken',
  'preset',
  'faceOnHit',
  'sparkOnHit',
  'hitPreset',
  'breakStyle',
  'crackOnHit',
  'crackStyle',
  'shakeOnHit',
] as const;

const FLOAT_FIELDS = [
  'range',
  'impactFraction',
  'pendingImpact',
  'burstCount',
  'hitBurstCount',
  'cutHeight',
  'popupColorR',
  'popupColorG',
  'popupColorB',
  'popupSize',
] as const;

describe('Destructible component shape', () => {
  it('exposes exactly the documented SOA fields', () => {
    const keys = Object.keys(Destructible).sort();
    expect(keys).toEqual([...UINT8_FIELDS, ...FLOAT_FIELDS].sort());
  });

  for (const field of UINT8_FIELDS) {
    it(`${field} is Uint8Array(MAX_ENTITIES)`, () => {
      expect(Destructible[field]).toBeInstanceOf(Uint8Array);
      expect(Destructible[field].length).toBe(MAX_ENTITIES);
    });
  }

  for (const field of FLOAT_FIELDS) {
    it(`${field} is Float32Array(MAX_ENTITIES)`, () => {
      expect(Destructible[field]).toBeInstanceOf(Float32Array);
      expect(Destructible[field].length).toBe(MAX_ENTITIES);
    });
  }
});

describe('Destructible per-entity round-trip', () => {
  for (let slot = 0; slot < 25; slot++) {
    it(`uint8 fields round-trip on entity ${slot}`, () => {
      for (const field of UINT8_FIELDS) {
        const v = (slot + field.length) % 256;
        Destructible[field][slot] = v;
        expect(Destructible[field][slot]).toBe(v);
        Destructible[field][slot] = 0;
      }
    });

    it(`float fields round-trip on entity ${slot}`, () => {
      for (const field of FLOAT_FIELDS) {
        const v = slot * 0.17 + field.length * 0.01;
        Destructible[field][slot] = v;
        expect(Destructible[field][slot]).toBeCloseTo(v, 5);
        Destructible[field][slot] = 0;
      }
    });
  }
});
