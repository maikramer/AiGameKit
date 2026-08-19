import {
  defineComponent,
  F32,
  U32,
  U8,
} from '../../core/ecs/component-storage';

/**
 * `<Weather>` — global weather controller (one per scene). Declarative
 * baseline for wind / cloud coverage / rain; runtime changes go through
 * `setWeather()` and biome `rain` overrides.
 */
export const WeatherComponent = defineComponent({
  windDirX: F32,
  windDirZ: F32,
  /** Wind speed, m/s (drives cloud drift + rain slant). */
  windStrength: F32,
  /** Cloud coverage 0..1. */
  clouds: F32,
  cloudHeight: F32,
  /** Baseline rain 0..1 (biomes can raise it). */
  rain: F32,
  /** 1 = slow ambient cloud cycle (coverage breathes over minutes). */
  cycle: U8,
  /**
   * Optional deterministic seed for cloud/rain placement. 0 = Math.random
   * (backward compatible); any non-zero value makes the cloud field and rain
   * distribution reproducible across reloads.
   */
  seed: U32,
  /** Internal: 1 once the runtime picked up the declarative values. */
  seeded: U8,
});
