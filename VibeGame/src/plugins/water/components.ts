import {
  defineComponent,
  F32,
  U32,
  U8,
} from '../../core/ecs/component-storage';
import type { State } from '../../core';

/**
 * `<Lake>` — a sculpted body of water. Not a global height-plane: each lake
 * carves a smooth bowl into the terrain height sampler at its own elevation
 * (mesh, physics heightfields, BVH and height queries all read the sampler,
 * so they inherit the depression), then fills it with a local water surface.
 */
export const Lake = defineComponent({
  /** Bowl radius in metres. */
  radius: F32,
  /** Bowl depth below the water surface, metres. */
  depth: F32,
  /** Water surface distance below the lowest rim point, metres. */
  waterOffset: F32,
  /** Water tint (hex). */
  color: U32,
  /** Water surface opacity 0..1. */
  opacity: F32,
  /** Ripple animation strength (0 = still water). */
  ripple: F32,
  /**
   * Wave amplitude in metres. 0 (default) = auto: scales with the lake radius
   * so small ponds barely stir while big lakes visibly swell.
   */
  waveHeight: F32,
  /** Wave/shimmer animation speed multiplier (1 = default pacing). */
  waveSpeed: F32,
  /** Resolved world Y of the water surface (set by the system). */
  waterY: F32,
  /** 1 once the carve + surface have been applied. */
  applied: U8,
});

/**
 * `<River>` — a sculpted river channel along a polyline. Shares the water
 * material and registry with lakes; the path generalises the lake disc to a
 * ribbon. The path itself lives in a side-channel (bitecs can't store arrays).
 */
export const River = defineComponent({
  /** Waterline width (m) — the visible water; the carve is wider by the banks. */
  width: F32,
  /** Channel depth below the water surface (m). */
  depth: F32,
  /** Water surface distance below the lowest bank point (m). */
  waterOffset: F32,
  /** Exposed carved-bank width each side of the waterline (m). */
  bankWidth: F32,
  /** Bank crest height above the water surface (m) — the freeboard. */
  bankHeight: F32,
  /** Water tint (hex). */
  color: U32,
  /** Water surface opacity 0..1. */
  opacity: F32,
  /** Ripple animation strength (0 = still water). */
  ripple: F32,
  /** Wave amplitude in metres. */
  waveHeight: F32,
  /** Wave/shimmer animation speed multiplier. */
  waveSpeed: F32,
  /** Resolved world Y of the water surface (set by the system). */
  waterY: F32,
  /** 1 once the carve + surface have been applied. */
  applied: U8,
});

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
