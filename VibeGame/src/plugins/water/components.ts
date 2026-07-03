import { MAX_ENTITIES } from '../../core/ecs/constants';

/**
 * `<Lake>` — a sculpted body of water. Not a global height-plane: each lake
 * carves a smooth bowl into the terrain height sampler at its own elevation
 * (mesh, physics heightfields, BVH and height queries all read the sampler,
 * so they inherit the depression), then fills it with a local water surface.
 */
export const Lake = {
  /** Bowl radius in metres. */
  radius: new Float32Array(MAX_ENTITIES),
  /** Bowl depth below the water surface, metres. */
  depth: new Float32Array(MAX_ENTITIES),
  /** Water surface distance below the lowest rim point, metres. */
  waterOffset: new Float32Array(MAX_ENTITIES),
  /** Water tint (hex). */
  color: new Uint32Array(MAX_ENTITIES),
  /** Water surface opacity 0..1. */
  opacity: new Float32Array(MAX_ENTITIES),
  /** Ripple animation strength (0 = still water). */
  ripple: new Float32Array(MAX_ENTITIES),
  /**
   * Wave amplitude in metres. 0 (default) = auto: scales with the lake radius
   * so small ponds barely stir while big lakes visibly swell.
   */
  waveHeight: new Float32Array(MAX_ENTITIES),
  /** Wave/shimmer animation speed multiplier (1 = default pacing). */
  waveSpeed: new Float32Array(MAX_ENTITIES),
  /** Resolved world Y of the water surface (set by the system). */
  waterY: new Float32Array(MAX_ENTITIES),
  /** 1 once the carve + surface have been applied. */
  applied: new Uint8Array(MAX_ENTITIES),
} as const;
