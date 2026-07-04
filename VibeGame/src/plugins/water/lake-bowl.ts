import type * as THREE from 'three';
import type { HeightSampler } from '../terrain/height-sampler';
import type { WorldAabb } from '../terrain/density-map';
import { carveBowl, rimHeight, shoreFraction } from './carve';
import { makeLakeGeometry } from './systems';
import type { WaterBody } from './registry';
import type { WaterShape, WaterShapeResult } from './water-shape';

export interface LakeBowlOpts {
  /** Lake centre in field-local coords (for carve + geometry seed). */
  localX: number;
  localZ: number;
  /** Lake centre in world coords (for mesh placement + registry). */
  worldX: number;
  worldZ: number;
  radius: number;
  depth: number;
  waterOffset: number;
}

/**
 * Lake water shape: a sculpted bowl. Wraps the existing carveBowl + rimHeight +
 * makeLakeGeometry so lakes flow through the same WaterShape pipeline as rivers
 * without changing their behaviour.
 */
export class LakeBowl implements WaterShape {
  private readonly rimMargin = 1.3; // covers shapeRadius overshoot (amplitude 0.28)
  /** Local water-surface Y, captured during carve() for mesh placement. */
  private localWaterY = 0;

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
    this.localWaterY = waterY;
    const carved = carveBowl(sampler, localX, localZ, radius, rimY, depth);
    return { carved, rimY, waterY };
  }

  buildGeometry(): THREE.BufferGeometry {
    const { localX, localZ, radius } = this.opts;
    // seedX/seedZ = lake centre in local carve space (drives shapeRadius).
    // The fan is origin-centred; worldOrigin() tells applyWaterShape where to place it.
    return makeLakeGeometry(radius, localX, localZ);
  }

  worldOrigin(worldOffsetY: number): { x: number; y: number; z: number } {
    // The fan is origin-centred and flat (Y=0); lift it to the world water Y.
    return {
      x: this.opts.worldX,
      y: worldOffsetY + this.localWaterY,
      z: this.opts.worldZ,
    };
  }

  densityBoost(): number {
    return 255;
  }

  toWaterBody(worldWaterY: number): WaterBody {
    const { worldX, worldZ, radius, depth, waterOffset } = this.opts;
    const shoreR = shoreFraction(depth, waterOffset) * radius;
    return {
      kind: 'lake',
      x: worldX,
      z: worldZ,
      radius,
      shoreRadius: shoreR,
      waterY: worldWaterY,
    };
  }
}
