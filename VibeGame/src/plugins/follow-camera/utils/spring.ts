const TAU = 2 * Math.PI;
const MAX_DT = 0.1;

export interface SpringResult {
  value: number;
  velocity: number;
}

/**
 * Advance a 1-D critically-damped spring by one time-step.
 *
 * @param current  - Current value.
 * @param target   - Target value.
 * @param velocity - Current velocity (mutated in-place for the returned result).
 * @param dt       - Delta time in seconds.
 * @param springTime   - Time constant (seconds). Lower = snappier. Default 0.15.
 * @param dampingRatio - 1.0 = critically damped (default). <1 = underdamped (bouncy).
 * @returns {{ value: number, velocity: number }}
 */
export function springStep(
  current: number,
  target: number,
  velocity: number,
  dt: number,
  springTime = 0.15,
  dampingRatio = 1.0
): SpringResult {
  const frameDt = Math.min(dt, MAX_DT);

  const omega = TAU / springTime;
  const stiffness = omega * omega;
  const damping = 2 * dampingRatio * omega;

  // Sub-step for Euler stability: max stable step ≈ springTime / TAU.
  const maxStableDt = springTime / TAU;
  const steps = Math.max(1, Math.ceil(frameDt / maxStableDt));
  const subDt = frameDt / steps;

  let v = velocity;
  let c = current;

  for (let i = 0; i < steps; i++) {
    const displacement = target - c;
    const accel = stiffness * displacement - damping * v;
    v += accel * subDt;

    // Hard cap: prevent absurd velocities while allowing natural oscillation.
    const cap = Math.abs(displacement) * omega * 50;
    if (cap > 0 && Math.abs(v) > cap) {
      v = Math.sign(v) * cap;
    }

    c += v * subDt;
  }

  return { value: c, velocity: v };
}
