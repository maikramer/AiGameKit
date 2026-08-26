/**
 * Pure sun math — no ECS, no THREE. Angles in DEGREES to match
 * `ProceduralSky` fields; the sky plugin converts to direction.
 */

export interface SunArcOptions {
  dawnMinute: number;
  duskMinute: number;
  sunAzimuthBase: number;
  maxSunElevation: number;
  minSunElevation: number;
}

export interface SunAngles {
  elevation: number;
  azimuth: number;
}

/**
 * Sun position for a minute of the day.
 *
 * Day (dawn→dusk): elevation sweeps `min → max → min` on a sine — a smooth
 * arc with the peak at solar noon. Night mirrors the arc BELOW the horizon
 * (`min − span·sin`), so dusk and dawn join continuously at `min`.
 * Azimuth sweeps `base−90°` at dawn → `base+90°` at dusk (east to west over
 * the daylight span), continuing around the circle through the night so the
 * position stays continuous. No astronomy — a believable arc, not an
 * ephemeris.
 */
export function sunAngles(minuteOfDay: number, opts: SunArcOptions): SunAngles {
  const m = mod1440(minuteOfDay);
  const dawn = mod1440(opts.dawnMinute);
  let dusk = mod1440(opts.duskMinute);
  if (dusk <= dawn) dusk += 1440; // dusk past midnight

  const dayLen = Math.max(1e-6, dusk - dawn);
  const nightLen = Math.max(1e-6, 1440 - dayLen);
  const span = Math.max(1e-6, opts.maxSunElevation - opts.minSunElevation);

  // Day-relative minute: dawn..dusk is the day half, (dusk..dawn+1440) night.
  const md = m >= dawn ? m : m + 1440;
  const isDay = md >= dawn && md <= dusk;

  let elevation: number;
  let azimuthTurns: number;
  if (isDay) {
    const t = (md - dawn) / dayLen;
    elevation = opts.minSunElevation + span * Math.sin(Math.PI * t);
    azimuthTurns = t;
  } else {
    const sinceDusk = md - dusk;
    const t = sinceDusk / nightLen;
    elevation = opts.minSunElevation - span * Math.sin(Math.PI * t);
    azimuthTurns = 1 + t;
  }

  const azimuth = opts.sunAzimuthBase - 90 + 180 * azimuthTurns;
  return { elevation, azimuth };
}

/** 0 when the sun is at/below the horizon, 1 at full day — for ambient ramps. */
export function daylightFactor(elevationDeg: number): number {
  // Ramp across the twilight band: pitch dark at −8°, full day at +10°.
  return clamp01((elevationDeg + 8) / 18);
}

/** Quantize an angle to the write step — the PMREM thrash brake. */
export function quantizeAngle(deg: number, stepDeg: number): number {
  if (!(stepDeg > 0)) return deg;
  return Math.round(deg / stepDeg) * stepDeg;
}

function mod1440(m: number): number {
  return ((m % 1440) + 1440) % 1440;
}

function clamp01(v: number): number {
  return v < 0 ? 0 : v > 1 ? 1 : v;
}
