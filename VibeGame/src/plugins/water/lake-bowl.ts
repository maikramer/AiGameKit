import type * as THREE from 'three';
import type { HeightSampler } from '../terrain/height-sampler';
import type { WorldAabb } from '../terrain/density-map';
import { carveBowl, rimHeight, shoreFraction } from './carve';
import { makeLakeGeometry } from './systems';
import type { WaterBody } from './registry';
import type { WaterShape, WaterShapeResult } from './water-shape';

export interface LakeBowlOpts {
  localX: number;
  localZ: number;
  radius: number;
  depth: number;
  waterOffset: number;
}

/**
 * Lake water shape: a sculpted bowl. Wraps the existing carveBowl + rimHeight +
 * makeLakeGeometry so lakes flow through the same WaterShape pipeline as rivers
 * without changing their behaviour.
 *
 * The `aWaterT` attribute (radial distance / shaped shore radius, 0..1) is set
 * inside makeLakeGeometry (Task 6 bakes it there).
 */
export class LakeBowl implements WaterShape {
  private readonly rimMargin = 1.3; // covers shapeRadius overshoot (amplitude 0.28)

  constructor(private readonly opts: LakeBowlOpts) {}

  computeAabb(): WorldAabb {
    const { localX, localZ, radius } = this.opts;
    const m = radius * this.rimMargin;
    return {
      minX: localX - m,
      minZ: localZ - m,
      maxX: localX + m,
      maxZ: localZ + m,
    };
  }

  carve(sampler: HeightSampler): WaterShapeResult {
    const { localX, localZ, radius, depth, waterOffset } = this.opts;
    const rimY = rimHeight(sampler, localX, localZ, radius);
    const waterY = rimY - waterOffset;
    const carved = carveBowl(sampler, localX, localZ, radius, rimY, depth);
    return { carved, rimY, waterY };
  }

  buildGeometry(): THREE.BufferGeometry {
    const { localX, localZ, radius } = this.opts;
    // seedX/seedZ = lake centre in local carve space (drives shapeRadius).
    return makeLakeGeometry(radius, localX, localZ);
  }

  densityBoost(): number {
    return 255;
  }

  toWaterBody(worldWaterY: number): WaterBody {
    const { localX, localZ, radius, depth, waterOffset } = this.opts;
    const shoreR = shoreFraction(depth, waterOffset) * radius;
    return {
      kind: 'lake',
      x: localX,
      z: localZ,
      radius,
      shoreRadius: shoreR,
      waterY: worldWaterY,
    };
  }
}
