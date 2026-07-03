import type * as THREE from 'three';
import type { HeightSampler } from '../terrain/height-sampler';
import type { WorldAabb } from '../terrain/density-map';
import { carveChannel, rimHeightAlongPath } from './carve';
import { makeRiverGeometry } from './river-geometry';
import { pathAabb } from './path-utils';
import type { WaterBody } from './registry';
import type { WaterShape, WaterShapeResult } from './water-shape';

export interface RiverChannelOpts {
  /** Flat polyline `[x0,z0,...]` in field-local world coords. */
  path: number[];
  width: number;
  depth: number;
  waterOffset: number;
}

/** River water shape: a sculpted channel along a polyline. */
export class RiverChannel implements WaterShape {
  constructor(private readonly opts: RiverChannelOpts) {}

  computeAabb(): WorldAabb {
    return pathAabb(this.opts.path, this.opts.width / 2);
  }

  carve(sampler: HeightSampler): WaterShapeResult {
    const { path, width, depth, waterOffset } = this.opts;
    const rimY = rimHeightAlongPath(sampler, path, width);
    const waterY = rimY - waterOffset;
    const carved = carveChannel(sampler, path, width, rimY, depth);
    return { carved, rimY, waterY };
  }

  buildGeometry(): THREE.BufferGeometry {
    return makeRiverGeometry(this.opts.path, this.opts.width);
  }

  worldOrigin(): { x: number; z: number } {
    // Ribbon vertices already carry world coords, so the mesh sits at (0,0).
    return { x: 0, z: 0 };
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
      waterY: worldWaterY,
    };
  }
}
