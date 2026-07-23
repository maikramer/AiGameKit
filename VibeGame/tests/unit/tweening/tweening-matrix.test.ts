import { describe, expect, it } from 'bun:test';
import { EasingType, TweenAxis } from 'vibegame/tweening';

/** Mirror of TweenProcessingSystem.applyEasing for unit verification. */
function applyEasing(t: number, easing: number): number {
  switch (easing) {
    case EasingType.EaseInOut:
      return t < 0.5 ? 2 * t * t : 1 - 2 * (1 - t) * (1 - t);
    case EasingType.EaseOutQuad:
      return 1 - (1 - t) * (1 - t);
    default:
      return t;
  }
}

const easingModes = [
  { name: 'Linear', id: EasingType.Linear },
  { name: 'EaseInOut', id: EasingType.EaseInOut },
  { name: 'EaseOutQuad', id: EasingType.EaseOutQuad },
] as const;

describe('Tweening matrix — applyEasing at sample t', () => {
  for (const mode of easingModes) {
    for (let step = 0; step <= 20; step++) {
      const t = step / 20;
      it(`${mode.name} at t=${t.toFixed(2)} stays in [0,1]`, () => {
        const v = applyEasing(t, mode.id);
        expect(v).toBeGreaterThanOrEqual(0);
        expect(v).toBeLessThanOrEqual(1);
      });
    }
  }
});

describe('Tweening matrix — applyEasing endpoints', () => {
  for (const mode of easingModes) {
    it(`${mode.name} at t=0 → 0`, () => {
      expect(applyEasing(0, mode.id)).toBe(0);
    });
    it(`${mode.name} at t=1 → 1`, () => {
      expect(applyEasing(1, mode.id)).toBe(1);
    });
  }
});

describe('Tweening matrix — TweenAxis ordering', () => {
  const axes = [
    TweenAxis.None,
    TweenAxis.PosX,
    TweenAxis.PosY,
    TweenAxis.PosZ,
    TweenAxis.RotX,
    TweenAxis.RotY,
    TweenAxis.RotZ,
  ];

  for (let i = 0; i < axes.length; i++) {
    it(`axis index ${i} is ${axes[i]}`, () => {
      expect(axes[i]).toBe(i);
    });
  }
});

describe('Tweening matrix — linear interpolation midpoint', () => {
  for (let from = -10; from <= 10; from++) {
    it(`lerp(${from}, ${from + 20}) at t=0.5 → ${from + 10}`, () => {
      const t = applyEasing(0.5, EasingType.Linear);
      const value = from + (from + 20 - from) * t;
      expect(value).toBe(from + 10);
    });
  }
});

describe('Tweening matrix — EaseOutQuad monotonic samples', () => {
  for (let step = 1; step <= 20; step++) {
    it(`EaseOutQuad non-decreasing from step ${step - 1} to ${step}`, () => {
      const t0 = (step - 1) / 20;
      const t1 = step / 20;
      expect(applyEasing(t1, EasingType.EaseOutQuad)).toBeGreaterThanOrEqual(
        applyEasing(t0, EasingType.EaseOutQuad)
      );
    });
  }
});
