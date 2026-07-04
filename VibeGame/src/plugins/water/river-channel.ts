import type * as THREE from 'three';
import type { HeightSampler } from '../terrain/height-sampler';
import type { WorldAabb } from '../terrain/density-map';
import { carveRiverChannel, rimHeightAlongPath, shoreFraction } from './carve';
import { makeRiverGeometry } from './river-geometry';
import { pathAabb, resamplePath } from './path-utils';
import type { WaterBody } from './registry';
import type { WaterShape, WaterShapeResult } from './water-shape';
import { sampleHeightAt } from '../terrain/height-sampler';

export interface RiverChannelOpts {
  /** Flat polyline `[x0,z0,...]` in field-local world coords, SOURCE FIRST —
   *  the water surface only ever descends along the path order. */
  path: number[];
  width: number;
  depth: number;
  waterOffset: number;
  /** Carve-width multiplier (1.0 = carve matches water edge). Default 1.2. */
  carveMargin?: number;
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

/** Surface drop within one station that reads as a waterfall (m): the fall
 *  stations get full foam and the ribbon segment turns into a plunging sheet. */
const WATERFALL_DROP = 1.1;

/** Levee tuning: band width outside the channel, crest above the surface, and
 *  the max raise before we give up and let the drop read as a fall. */
const LEVEE_WIDTH = 2.5;
const LEVEE_LIP = 0.45;
const MAX_LEVEE_RAISE = 1.6;

/** Box-smooth `values` (returns a new array). */
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
  /** Extra foam weight per station (waterfalls/rapids), filled by carve(). */
  private foam: number[] = [];
  /** Post-carve sampler, for baking per-vertex ground clearance. */
  private sampler: HeightSampler | null = null;
  /** Field-local highest surface (rim − offset), for world-offset recovery. */
  private localWaterY = 0;
  /** Carve-width multiplier (carve wider than the water → exposed bank). */
  private readonly carveMarginValue: number;

  constructor(private readonly opts: RiverChannelOpts) {
    this.carveMarginValue = opts.carveMargin ?? 1.2;
  }

  carveMargin(): number {
    return this.carveMarginValue;
  }

  computeAabb(): WorldAabb {
    return pathAabb(
      this.opts.path,
      (this.opts.width * this.carveMarginValue) / 2 + LEVEE_WIDTH
    );
  }

  carve(sampler: HeightSampler): WaterShapeResult {
    const { path, width, depth, waterOffset } = this.opts;
    // Densify the authored polyline into ~3 m stations, then sample the
    // terrain axis height at every station from the UNMUTATED sampler — both
    // to drive the carve floor (stable across repeated carves → idempotent)
    // and the water surface ribbon.
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
    const smoothed = boxSmooth(axisHeights, SMOOTH_HALF_WINDOW);

    // Descending surface (prefix-min): water can never flow uphill. Through
    // terrain rises the level holds (the carve digs a gorge/pond); at sharp
    // terrain drops the level plunges — mark those stations as waterfalls so
    // the shader foams the sheet.
    const surface = new Array<number>(nodeCount);
    const foam = new Array<number>(nodeCount).fill(0);
    surface[0] = smoothed[0]! - waterOffset;
    for (let i = 1; i < nodeCount; i++) {
      const cand = smoothed[i]! - waterOffset;
      const prev = surface[i - 1]!;
      surface[i] = Math.min(prev, cand);
      const drop = prev - surface[i]!;
      if (drop > WATERFALL_DROP) {
        foam[i] = 1;
        foam[i - 1] = Math.max(foam[i - 1]!, 1);
        if (i + 1 < nodeCount) foam[i + 1] = 0.6;
        if (i >= 2) foam[i - 2] = Math.max(foam[i - 2]!, 0.6);
      }
    }

    this.stations = stations;
    this.surfaceHeights = surface;
    this.foam = foam;
    this.sampler = sampler;

    // rimHeightAlongPath guards the flat-sampler no-op and keeps the
    // "highest surface" datum for the registry consistent with lakes.
    const rimY = rimHeightAlongPath(sampler, stations, width);
    const waterY = rimY - waterOffset;
    this.localWaterY = waterY;
    const carved = carveRiverChannel(sampler, stations, surface, {
      width: width * this.carveMarginValue,
      channelDepth: Math.max(0.2, depth - waterOffset),
      leveeWidth: LEVEE_WIDTH,
      leveeLip: LEVEE_LIP,
      maxLeveeRaise: MAX_LEVEE_RAISE,
    });
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
    // visible bank a full-width ribbon would overhang the exposed carved
    // slope. Small pad so the in-shader alpha fade, not the polygon edge,
    // ends the water.
    const width = this.opts.width * Math.min(1, this.shoreT() + 0.08);
    // Per-vertex ground clearance from the POST-carve terrain: the shader
    // turns shallow contact (water grazing the banks/floor) into foam, giving
    // the ribbon a natural frothy outline instead of a hard polygon edge.
    let groundDepth: number[] | undefined;
    if (this.sampler && this.surfaceHeights.length > 0) {
      const s = this.sampler;
      const n = path.length / 2;
      groundDepth = new Array<number>(n * 3);
      const halfW = width / 2;
      for (let i = 0; i < n; i++) {
        const x = path[i * 2]!;
        const z = path[i * 2 + 1]!;
        const surfY = this.surfaceHeights[i] ?? 0;
        // Approximate the lateral offset with the segment normal used by the
        // ribbon builder (miter differences are sub-texel at 3 m stations).
        let nx = 0;
        let nz = 0;
        const j = Math.min(i, n - 2);
        const dx = path[(j + 1) * 2]! - path[j * 2]!;
        const dz = path[(j + 1) * 2 + 1]! - path[j * 2 + 1]!;
        const len = Math.hypot(dx, dz) || 1;
        nx = -dz / len;
        nz = dx / len;
        const depthAt = (px: number, pz: number): number =>
          Math.max(0, surfY - sampleHeightAt(s, px, pz));
        groundDepth[i * 3] = depthAt(x - nx * halfW, z - nz * halfW);
        groundDepth[i * 3 + 1] = depthAt(x, z);
        groundDepth[i * 3 + 2] = depthAt(x + nx * halfW, z + nz * halfW);
      }
    }
    return makeRiverGeometry(
      path,
      width,
      this.surfaceHeights,
      groundDepth,
      this.foam
    );
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
      // Highest surface point (source). Per-point queries should prefer
      // surfaceY, which tracks the descending profile station by station.
      waterY: worldWaterY,
      // surfaceHeights are field-local; recover the field's world Y offset
      // from the datum applyWaterShape resolved (worldWaterY = offset + local).
      surfaceY:
        this.surfaceHeights.length > 0
          ? this.surfaceHeights.map((h) => h + (worldWaterY - this.localWaterY))
          : undefined,
    };
  }
}
