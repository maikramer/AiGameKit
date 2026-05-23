import { describe, test, expect } from 'bun:test';
import { springStep } from '../../../../src/plugins/third-person-controller/utils/spring';

describe('springStep', () => {
  test('returns value and velocity', () => {
    const result = springStep(0, 10, 0, 0.016);
    expect(result).toHaveProperty('value');
    expect(result).toHaveProperty('velocity');
    expect(typeof result.value).toBe('number');
    expect(typeof result.velocity).toBe('number');
  });

  test('does not move when current equals target', () => {
    const result = springStep(5, 5, 0, 0.016);
    expect(result.value).toBeCloseTo(5, 5);
    expect(result.velocity).toBeCloseTo(0, 5);
  });

  test('converges to target within ~3 * springTime', () => {
    let current = 0;
    let velocity = 0;
    const target = 100;
    const springTime = 0.15;
    const dt = 0.016;
    const convergenceTime = 3 * springTime; // 0.45s
    const steps = Math.ceil(convergenceTime / dt);

    for (let i = 0; i < steps; i++) {
      const result = springStep(current, target, velocity, dt, springTime);
      current = result.value;
      velocity = result.velocity;
    }

    // Within 1% of target after 3 * springTime
    expect(Math.abs(current - target)).toBeLessThan(target * 0.01);
  });

  test('converges from above (negative displacement)', () => {
    let current = 100;
    let velocity = 0;
    const target = 0;
    const springTime = 0.15;
    const dt = 0.016;
    const steps = Math.ceil((3 * springTime) / dt);

    for (let i = 0; i < steps; i++) {
      const result = springStep(current, target, velocity, dt, springTime);
      current = result.value;
      velocity = result.velocity;
    }

    expect(Math.abs(current - target)).toBeLessThan(1);
  });

  test('dt clamping prevents overshoot on huge frame spikes', () => {
    let current = 0;
    let velocity = 0;
    const target = 10;
    const springTime = 0.15;

    // Simulate a 2-second frame spike — should be clamped internally to 0.1
    const result = springStep(current, target, velocity, 2.0, springTime);

    // With dt clamped to 0.1, value should not overshoot wildly past target
    expect(result.value).toBeLessThan(target + 50);
    expect(result.value).toBeGreaterThan(-50);
  });

  test('velocity capping prevents extreme jumps', () => {
    let current = 0;
    const target = 1;
    const springTime = 0.15;

    // Feed an absurd velocity — cap should prevent explosion
    const result = springStep(current, target, 1_000_000, 0.016, springTime);

    // Without cap, value would jump by 1M * 0.016 = 16000. With cap, much less.
    expect(Math.abs(result.value - current)).toBeLessThan(50);
  });

  test('critically damped (default) settles without oscillation', () => {
    let current = 0;
    let velocity = 0;
    const target = 50;
    const springTime = 0.15;
    const dt = 0.016;
    const totalSteps = Math.ceil((5 * springTime) / dt);
    let crossedTarget = false;
    let oscillations = 0;

    let prevSign = Math.sign(target - current);

    for (let i = 0; i < totalSteps; i++) {
      const result = springStep(current, target, velocity, dt, springTime, 1.0);
      current = result.value;
      velocity = result.velocity;

      const sign = Math.sign(target - current);
      if (crossedTarget && sign !== prevSign) {
        oscillations++;
      }
      if (Math.abs(target - current) < 0.1) {
        crossedTarget = true;
      }
      prevSign = sign;
    }

    // Critically damped should have at most 1 very small crossing (settling)
    expect(oscillations).toBeLessThanOrEqual(1);
  });

  test('underdamped oscillates', () => {
    let current = 0;
    let velocity = 0;
    const target = 50;
    const springTime = 0.15;
    const dt = 0.016;
    const totalSteps = Math.ceil((5 * springTime) / dt);
    let crossings = 0;
    let prevDiff = target - current;

    for (let i = 0; i < totalSteps; i++) {
      const result = springStep(current, target, velocity, dt, springTime, 0.2);
      current = result.value;
      velocity = result.velocity;

      const diff = target - current;
      if (prevDiff * diff < 0) {
        crossings++;
      }
      prevDiff = diff;
    }

    expect(crossings).toBeGreaterThan(2);
  });

  test('snappier spring (smaller springTime) converges faster', () => {
    function simulate(springTime: number): number {
      let c = 0;
      let v = 0;
      const steps = 500;
      for (let i = 0; i < steps; i++) {
        const r = springStep(c, 100, v, 0.016, springTime);
        c = r.value;
        v = r.velocity;
      }
      return Math.abs(c - 100);
    }

    const fast = simulate(0.05);
    const slow = simulate(0.5);

    expect(fast).toBeLessThan(slow);
  });
});
