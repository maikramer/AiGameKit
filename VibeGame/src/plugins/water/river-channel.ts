import type * as THREE from 'three';
import type { HeightSampler } from '../terrain/height-sampler';
import type { WorldAabb } from '../terrain/density-map';
import { carveChannel, rimHeightAlongPath, shoreFraction } from './carve';
import { makeRiverGeometry } from './river-geometry';
import { pathAabb, resamplePath } from './path-utils';
import type { WaterBody } from './registry';
import type { WaterShape, WaterShapeResult } from './water-shape';
import { sampleHeightAt } from '../terrain/height-sampler';

export interface RiverChannelOpts {
  /** Flat polyline `[x0,z0,...]` in field-local world coords. */
  path: number[];
  width: number;
  depth: number;
  waterOffset: number;
}

/**
 * Spacing (metres) between resampled path stations. Authored river nodes sit
 * tens of metres apart; carve and surface both need terrain samples every few
 * metres or the channel/ribbon turns into straight ramps between nodes that
 * float over dips and tunnel through rises.
 */
const STATION_SPACING = 3;

/**
 * Half-window (in stations) of the box smoothing applied to the axis heights.
 * ±2 stations at 3 m spacing ≈ a 15 m window: kills per-texel terrain jitter
 * so the water surface reads as calm water, while still following the valley.
 */
const SMOOTH_HALF_WINDOW = 2;

/** Box-smooth `values` in place-safe fashion (returns a new array). */
function boxSmooth(values: number[], halfWindow: number): number[] {
  const n = values.length;
  const out = new Array<number>(n);
  for (let i = 0; i < n; i++) {
    let sum = 0;
    let count = 0;
    const lo = Math.max(0, i - halfWindow);
    const hi = Math.min(n - 1, i + halfWindow);
    for (let j = lo; j <= hi; j++) {
      sum += values[j]!;
      count++;
    }
    out[i] = sum / count;
  }
  return out;
}

/** River water shape: a sculpted channel along a polyline. */
export class RiverChannel implements WaterShape {
  /** Dense resampled stations (`[x0,z0,...]`), filled by carve(). */
  private stations: number[] = [];
  /** Water-surface height per station (field-local), filled by carve(). */
  private surfaceHeights: number[] = [];

  constructor(private readonly opts: RiverChannelOpts) {}

  computeAabb(): WorldAabb {
    return pathAabb(this.opts.path, this.opts.width / 2);
  }

  carve(sampler: HeightSampler): WaterShapeResult {
    const { path, width, depth, waterOffset } = this.opts;
    // Densify the authored polyline into ~3 m stations, then sample the
    // terrain axis height at every station from the UNMUTATED sampler — both
    // to drive the terrain-following floor (stable across repeated carves →
    // idempotent) and the water surface ribbon. Sparse per-node sampling is
    // not enough: between nodes the linear ramp floats over dips and buries
    // itself in rises.
    const stations = resamplePath(path, STATION_SPACING);
    const nodeCount = stations.length / 2;
    const axisHeights = new Array<number>(nodeCount);
    for (let i = 0; i < nodeCount; i++) {
      axisHeights[i] = sampleHeightAt(
        sampler,
        stations[i * 2]!,
        stations[i * 2 + 1]!
      );
    }
    // Smooth so the surface reads as water, not a cloth draped over bumps.
    const smoothed = boxSmooth(axisHeights, SMOOTH_HALF_WINDOW);
    this.stations = stations;
    this.surfaceHeights = smoothed.map((h) => h - waterOffset);
    // rimHeightAlongPath keeps bank-probe behaviour consistent with lakes and
    // guards the flat-sampler no-op; carveChannel digs the floor `depth` below
    // the smoothed axis line, so the channel always contains the surface.
    const rimY = rimHeightAlongPath(sampler, stations, width);
    const waterY = rimY - waterOffset;
    const carved = carveChannel(
      sampler,
      stations,
      width,
      rimY,
      depth,
      smoothed
    );
    return { carved, rimY, waterY };
  }

  /**
   * Fraction of the half-width where the carved floor meets the water surface
   * (the waterline). 0 (degenerate: surface at the rim) falls back to 0.95 so
   * the ribbon still spans the channel.
   */
  private shoreT(): number {
    const t = shoreFraction(this.opts.depth, this.opts.waterOffset);
    return t > 0 ? t : 0.95;
  }

  buildGeometry(): THREE.BufferGeometry {
    // Prefer the dense carve stations; fall back to the authored path when
    // buildGeometry is called without a carve (tests/legacy flat ribbon).
    const path = this.stations.length >= 4 ? this.stations : this.opts.path;
    // Size the ribbon to the waterline, not the full carved channel: with a
    // visible bank (waterOffset a decent fraction of depth) a full-width
    // ribbon would overhang the exposed carved slope. Small pad so the
    // in-shader alpha fade (t 0.9–1), not the polygon edge, ends the water.
    const width = this.opts.width * Math.min(1, this.shoreT() + 0.08);
    return makeRiverGeometry(path, width, this.surfaceHeights);
  }

  worldOrigin(worldOffsetY: number): { x: number; y: number; z: number } {
    // Ribbon vertices carry world X/Z and field-local surface Y (per station).
    // The mesh only needs the field's world Y offset to lift field-local into world.
    return { x: 0, y: worldOffsetY, z: 0 };
  }

  densityBoost(): number {
    return 255;
  }

  toWaterBody(worldWaterY: number): WaterBody {
    // Registry gets the dense stations when available so distance queries
    // (spawner in-water, splash/drag) track curves, not just authored nodes.
    const src = this.stations.length >= 4 ? this.stations : this.opts.path;
    const pairs: Array<readonly [number, number]> = [];
    for (let i = 0; i < src.length; i += 2) {
      pairs.push([src[i]!, src[i + 1]!]);
    }
    return {
      kind: 'river',
      path: pairs,
      width: this.opts.width,
      shoreWidth: this.opts.width * this.shoreT(),
      // worldWaterY passed by applyWaterShape = worldOffset.y + (rimY - waterOffset).
      // It's the highest surface point; queries use it as the water level.
      waterY: worldWaterY,
    };
  }
}
