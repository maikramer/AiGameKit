import type * as THREE from 'three';
import type { HeightSampler } from '../terrain/height-sampler';
import type { WorldAabb } from '../terrain/density-map';
import type { WaterBody } from './registry';

/** Result of carving a water shape into the terrain sampler. */
export interface WaterShapeResult {
  /** false when the sampler was flat/dataless — caller should retry next frame. */
  carved: boolean;
  /** Crest height of the rim/margins (metres). */
  rimY: number;
  /** Water surface height in field-local space (metres). */
  waterY: number;
}

/**
 * A water shape (lake bowl, river channel, future swamp/coastline) presented
 * through a uniform interface so the common apply flow (density boost, carve,
 * mark dirty, spawn mesh, register body, cleanup) can live in one helper.
 *
 * The only shape-specific behaviour is the "distance from the margin" metric:
 * lakes compute it radially, rivers laterally along the path. The water
 * material is shape-agnostic because that metric (`aWaterT`) is baked into
 * the geometry by `buildGeometry()`.
 */
export interface WaterShape {
  /** AABB in field-local coords (X/Z), for density boost + chunk invalidation. */
  computeAabb(): WorldAabb;
  /** Carve the shape into the sampler in place (heights only go down). */
  carve(sampler: HeightSampler): WaterShapeResult;
  /** Surface mesh geometry. Must set the `aWaterT` attribute (0=center, 1=margin). */
  buildGeometry(): THREE.BufferGeometry;
  /** Density boost for terrain chunks overlapping this shape (255 = max detail). */
  densityBoost(): number;
  /**
   * Water body for the registry. `worldWaterY` is the resolved world-space
   * surface height (= field.worldOffset.y + carve waterY), supplied by
   * `applyWaterShape` because only it knows the field's world offset.
   */
  toWaterBody(worldWaterY: number): WaterBody;
}

/** Visual config shared by all water shapes for the material. */
export interface WaterMaterialConfig {
  color: number;
  opacity: number;
  ripple: number;
  waveHeight: number;
  waveSpeed: number;
}
