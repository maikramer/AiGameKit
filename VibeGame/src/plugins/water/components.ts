import { MAX_ENTITIES } from '../../core/ecs/constants';
import type { State } from '../../core';

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

/**
 * `<River>` — a sculpted river channel along a polyline. Shares the water
 * material and registry with lakes; the path generalises the lake disc to a
 * ribbon. The path itself lives in a side-channel (bitecs can't store arrays).
 */
export const River = {
  /** Channel width (m). */
  width: new Float32Array(MAX_ENTITIES),
  /** Channel depth below the water surface (m). */
  depth: new Float32Array(MAX_ENTITIES),
  /** Water surface distance below the lowest bank point (m). */
  waterOffset: new Float32Array(MAX_ENTITIES),
  /** Water tint (hex). */
  color: new Uint32Array(MAX_ENTITIES),
  /** Water surface opacity 0..1. */
  opacity: new Float32Array(MAX_ENTITIES),
  /** Ripple animation strength (0 = still water). */
  ripple: new Float32Array(MAX_ENTITIES),
  /** Wave amplitude in metres. */
  waveHeight: new Float32Array(MAX_ENTITIES),
  /** Wave/shimmer animation speed multiplier. */
  waveSpeed: new Float32Array(MAX_ENTITIES),
  /** Resolved world Y of the water surface (set by the system). */
  waterY: new Float32Array(MAX_ENTITIES),
  /** 1 once the carve + surface have been applied. */
  applied: new Uint8Array(MAX_ENTITIES),
} as const;

/** Side-channel for river paths (bitecs can't store arrays). Flat `[x0,z0,...]`. */
const RIVER_PATHS = new WeakMap<State, Map<number, number[]>>();

export function getRiverPath(state: State, entity: number): number[] {
  return RIVER_PATHS.get(state)?.get(entity) ?? [];
}

export function setRiverPath(
  state: State,
  entity: number,
  path: number[]
): void {
  let m = RIVER_PATHS.get(state);
  if (!m) {
    m = new Map();
    RIVER_PATHS.set(state, m);
  }
  m.set(entity, path);
}
