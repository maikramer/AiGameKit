import type * as THREE from 'three';
import type { HeightSampler } from '../terrain/height-sampler';
import type { WorldAabb } from '../terrain/density-map';
import { carveChannel, rimHeightAlongPath } from './carve';
import { makeRiverGeometry } from './river-geometry';
import { pathAabb } from './path-utils';
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

/** River water shape: a sculpted channel along a polyline. */
export class RiverChannel implements WaterShape {
  /** Water-surface height per path node (field-local), filled by carve(). */
  private surfaceHeights: number[] = [];

  constructor(private readonly opts: RiverChannelOpts) {}

  computeAabb(): WorldAabb {
    return pathAabb(this.opts.path, this.opts.width / 2);
  }

  carve(sampler: HeightSampler): WaterShapeResult {
    const { path, width, depth, waterOffset } = this.opts;
    // Sample the terrain axis height at every path node from the UNMUTATED
    // sampler — both to drive the terrain-following floor (stable across
    // repeated carves → idempotent) and the descending water surface ribbon.
    const nodeCount = path.length / 2;
    const axisHeights = new Array<number>(nodeCount);
    this.surfaceHeights = new Array<number>(nodeCount);
    for (let i = 0; i < nodeCount; i++) {
      const axisY = sampleHeightAt(sampler, path[i * 2]!, path[i * 2 + 1]!);
      axisHeights[i] = axisY;
      this.surfaceHeights[i] = axisY - waterOffset;
    }
    // rimHeightAlongPath keeps bank-probe behaviour consistent with lakes and
    // guards the flat-sampler no-op; carveChannel uses axisHeights for the floor.
    const rimY = rimHeightAlongPath(sampler, path, width);
    const waterY = rimY - waterOffset;
    const carved = carveChannel(sampler, path, width, rimY, depth, axisHeights);
    return { carved, rimY, waterY };
  }

  buildGeometry(): THREE.BufferGeometry {
    return makeRiverGeometry(
      this.opts.path,
      this.opts.width,
      this.surfaceHeights
    );
  }

  worldOrigin(worldOffsetY: number): { x: number; y: number; z: number } {
    // Ribbon vertices carry world X/Z and field-local surface Y (per node).
    // The mesh only needs the field's world Y offset to lift field-local into world.
    return { x: 0, y: worldOffsetY, z: 0 };
  }

  densityBoost(): number {
    return 255;
  }

  toWaterBody(worldWaterY: number): WaterBody {
    // Convert flat path to [x,z] pairs for the registry.
    const pairs: Array<readonly [number, number]> = [];
    for (let i = 0; i < this.opts.path.length; i += 2) {
      pairs.push([this.opts.path[i]!, this.opts.path[i + 1]!]);
    }
    return {
      kind: 'river',
      path: pairs,
      width: this.opts.width,
      // worldWaterY passed by applyWaterShape = worldOffset.y + (rimY - waterOffset).
      // It's the highest surface point; queries use it as the water level.
      waterY: worldWaterY,
    };
  }
}
