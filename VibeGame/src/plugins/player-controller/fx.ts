// Trauma-based screen shake: callers `addCameraShake(amount)` on impact and
// the third-person camera system layers a decaying offset + micro-roll on top
// of its smoothed follow. Amplitude scales with trauma² so light hits stay
// subtle while heavy blows spike (Squirrel Eiserloh's model). State is global
// on purpose — any game system can kick the camera without threading camera
// entities around. Decay/sampling run on UNSCALED time so the view keeps
// trembling through a hit-stop freeze.

/** Trauma lost per second. */
const SHAKE_DECAY_PER_SEC = 1.4;
/** Max positional offset (m) at trauma = 1. */
const SHAKE_MAX_OFFSET = 0.22;
/** Max camera roll (rad) at trauma = 1. */
const SHAKE_MAX_ROLL = 0.03;

let trauma = 0;

/** Register impact trauma (clamped to 1; overlapping hits accumulate). */
export function addCameraShake(amount: number): void {
  if (!(amount > 0)) return;
  trauma = Math.min(1, trauma + amount);
}

export function getCameraShakeTrauma(): number {
  return trauma;
}

/** Decay trauma. Call once per frame with **unscaled** dt. */
export function tickCameraShake(unscaledDt: number): void {
  if (trauma <= 0) return;
  trauma = Math.max(0, trauma - unscaledDt * SHAKE_DECAY_PER_SEC);
}

export interface CameraShakeSample {
  x: number;
  y: number;
  z: number;
  roll: number;
}

const _sample: CameraShakeSample = { x: 0, y: 0, z: 0, roll: 0 };

/**
 * Current shake offset (m) + roll (rad) written into `out`. Layered sines
 * stand in for Perlin noise — cheap, smooth, no correlation between axes.
 * `time` must be real (unscaled) so the shake keeps evolving while the world
 * is frozen by a hit-stop.
 */
export function cameraShakeSample(
  time: number,
  out: CameraShakeSample = _sample
): CameraShakeSample {
  if (trauma <= 0.001) {
    out.x = 0;
    out.y = 0;
    out.z = 0;
    out.roll = 0;
    return out;
  }
  const amp = trauma * trauma;
  const t = time * 24;
  out.x =
    (Math.sin(t * 1.3 + 0.7) * 0.6 + Math.sin(t * 2.9 + 2.1) * 0.4) *
    SHAKE_MAX_OFFSET *
    amp;
  out.y =
    (Math.sin(t * 1.7 + 4.2) * 0.6 + Math.sin(t * 3.3 + 1.3) * 0.4) *
    SHAKE_MAX_OFFSET *
    amp;
  out.z =
    (Math.sin(t * 1.1 + 2.8) * 0.6 + Math.sin(t * 2.3 + 5.0) * 0.4) *
    SHAKE_MAX_OFFSET *
    0.6 *
    amp;
  out.roll = Math.sin(t * 1.9 + 3.6) * SHAKE_MAX_ROLL * amp;
  return out;
}

/** Test/reset hook. */
export function resetCameraShake(): void {
  trauma = 0;
}
