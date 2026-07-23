import { describe, expect, it } from 'bun:test';
import {
  ANIMATION_CONFIG,
  ANIMATION_STATES,
  BODY_PARTS,
} from '../../../src/plugins/animation/constants';

const PART_NAMES = [
  'head',
  'torso',
  'leftArm',
  'rightArm',
  'leftLeg',
  'rightLeg',
] as const;

describe('BODY_PARTS structure', () => {
  for (const part of PART_NAMES) {
    it(`${part} defines size x/y/z`, () => {
      const p = BODY_PARTS[part];
      expect(p.size.x).toBeGreaterThan(0);
      expect(p.size.y).toBeGreaterThan(0);
      expect(p.size.z).toBeGreaterThan(0);
    });
  }

  for (const part of PART_NAMES) {
    it(`${part} defines offset x/y/z`, () => {
      const p = BODY_PARTS[part];
      expect(Number.isFinite(p.offset.x)).toBe(true);
      expect(Number.isFinite(p.offset.y)).toBe(true);
      expect(Number.isFinite(p.offset.z)).toBe(true);
    });
  }

  for (const part of PART_NAMES) {
    it(`${part} color is a positive hex-ish number`, () => {
      expect(BODY_PARTS[part].color).toBeGreaterThan(0);
    });
  }

  it('arms are symmetric in X offset magnitude', () => {
    expect(Math.abs(BODY_PARTS.leftArm.offset.x)).toBeCloseTo(
      Math.abs(BODY_PARTS.rightArm.offset.x),
      5
    );
  });

  it('legs are symmetric in X offset magnitude', () => {
    expect(Math.abs(BODY_PARTS.leftLeg.offset.x)).toBeCloseTo(
      Math.abs(BODY_PARTS.rightLeg.offset.x),
      5
    );
  });
});

describe('ANIMATION_CONFIG scalars', () => {
  it('armSwingAngle is 30', () => {
    expect(ANIMATION_CONFIG.armSwingAngle).toBe(30);
  });

  it('legSwingAngle is 25', () => {
    expect(ANIMATION_CONFIG.legSwingAngle).toBe(25);
  });

  it('frequency is 0.5', () => {
    expect(ANIMATION_CONFIG.frequency).toBe(0.5);
  });

  for (const key of [
    'armRaiseAngle',
    'bodyStretch',
    'legTuckAngle',
    'anticipationSquash',
    'anticipationDuration',
  ] as const) {
    it(`jump.${key} is defined`, () => {
      expect(ANIMATION_CONFIG.jump[key]).toBeDefined();
    });
  }

  for (const key of [
    'armFlailAngle',
    'legDangleAngle',
    'bodyTiltAngle',
    'windSwayAmount',
  ] as const) {
    it(`fall.${key} is defined`, () => {
      expect(ANIMATION_CONFIG.fall[key]).toBeDefined();
    });
  }

  for (const key of ['duration', 'bounceHeight', 'squashAmount'] as const) {
    it(`landing.${key} is defined`, () => {
      expect(ANIMATION_CONFIG.landing[key]).toBeDefined();
    });
  }
});

describe('ANIMATION_STATES', () => {
  for (const [name, value] of Object.entries(ANIMATION_STATES)) {
    it(`${name} equals ${value}`, () => {
      expect(ANIMATION_STATES[name as keyof typeof ANIMATION_STATES]).toBe(
        value
      );
    });
  }

  it('IDLE is 0', () => {
    expect(ANIMATION_STATES.IDLE).toBe(0);
  });

  it('LANDING is 4', () => {
    expect(ANIMATION_STATES.LANDING).toBe(4);
  });
});
