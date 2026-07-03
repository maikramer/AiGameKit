import type { State } from '../../core';

/** Smoothed runtime weather (targets ramp in over a few seconds). */
export interface WeatherRuntime {
  /** Normalized wind direction. */
  windDirX: number;
  windDirZ: number;
  /** Wind speed, m/s. */
  windStrength: number;
  /** Cloud coverage 0..1 (current / target). */
  clouds: number;
  cloudsTarget: number;
  cloudHeight: number;
  /** Rain intensity 0..1 (current). */
  rain: number;
  /** Rain requested via API / weather cycle. */
  rainTarget: number;
  /** Rain requested by the active biome (blends via BiomeDetectionSystem). */
  environmentRain: number;
  /** Seconds for target ramps. */
  fadeSeconds: number;
}

const RUNTIME = new WeakMap<State, WeatherRuntime>();

export function getWeather(state: State): WeatherRuntime {
  let w = RUNTIME.get(state);
  if (!w) {
    w = {
      windDirX: 1,
      windDirZ: 0,
      windStrength: 0,
      clouds: 0,
      cloudsTarget: 0,
      cloudHeight: 150,
      rain: 0,
      rainTarget: 0,
      environmentRain: 0,
      fadeSeconds: 4,
    };
    RUNTIME.set(state, w);
  }
  return w;
}

export interface WeatherPatch {
  windDirX?: number;
  windDirZ?: number;
  windStrength?: number;
  clouds?: number;
  cloudHeight?: number;
  rain?: number;
  fadeSeconds?: number;
}

/** Set weather targets; current values ramp over `fadeSeconds`. */
export function setWeather(state: State, patch: WeatherPatch): void {
  const w = getWeather(state);
  if (patch.windDirX !== undefined || patch.windDirZ !== undefined) {
    const x = patch.windDirX ?? w.windDirX;
    const z = patch.windDirZ ?? w.windDirZ;
    const len = Math.hypot(x, z) || 1;
    w.windDirX = x / len;
    w.windDirZ = z / len;
  }
  if (patch.windStrength !== undefined) w.windStrength = patch.windStrength;
  if (patch.clouds !== undefined) w.cloudsTarget = patch.clouds;
  if (patch.cloudHeight !== undefined) w.cloudHeight = patch.cloudHeight;
  if (patch.rain !== undefined) w.rainTarget = patch.rain;
  if (patch.fadeSeconds !== undefined) w.fadeSeconds = patch.fadeSeconds;
}

/**
 * Biome-driven rain (0..1). Kept separate from the API/cycle target — the
 * effective rain is `max(rainTarget, environmentRain)`, so a scripted storm
 * and a drizzly biome compose instead of overwriting each other.
 */
export function setEnvironmentRain(state: State, value: number): void {
  getWeather(state).environmentRain = value < 0 ? 0 : value > 1 ? 1 : value;
}

/** Effective rain target (API/cycle vs biome — the wetter one wins). */
export function effectiveRainTarget(w: WeatherRuntime): number {
  return Math.max(w.rainTarget, w.environmentRain);
}

/** World-space wind vector (direction × strength), for gameplay/FX. */
export function getWindVector(state: State): { x: number; z: number } {
  const w = getWeather(state);
  return { x: w.windDirX * w.windStrength, z: w.windDirZ * w.windStrength };
}
