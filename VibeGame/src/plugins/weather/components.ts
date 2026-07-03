import { MAX_ENTITIES } from '../../core/ecs/constants';

/**
 * `<Weather>` — global weather controller (one per scene). Declarative
 * baseline for wind / cloud coverage / rain; runtime changes go through
 * `setWeather()` and biome `rain` overrides.
 */
export const WeatherComponent = {
  windDirX: new Float32Array(MAX_ENTITIES),
  windDirZ: new Float32Array(MAX_ENTITIES),
  /** Wind speed, m/s (drives cloud drift + rain slant). */
  windStrength: new Float32Array(MAX_ENTITIES),
  /** Cloud coverage 0..1. */
  clouds: new Float32Array(MAX_ENTITIES),
  cloudHeight: new Float32Array(MAX_ENTITIES),
  /** Baseline rain 0..1 (biomes can raise it). */
  rain: new Float32Array(MAX_ENTITIES),
  /** 1 = slow ambient cloud cycle (coverage breathes over minutes). */
  cycle: new Uint8Array(MAX_ENTITIES),
  /** Internal: 1 once the runtime picked up the declarative values. */
  seeded: new Uint8Array(MAX_ENTITIES),
} as const;
