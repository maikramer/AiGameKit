/**
 * Ground-mutation pipeline — shared phases for pads, lakes, rivers, roads.
 *
 * Canonical order (every feature follows this):
 *
 *  1. **Design profile** — terrace / bowl / bank curve (feature-owned).
 *  2. **Density stamp** — {@link applyCorridorDensity} / {@link applyFeatureDensity}
 *     so leaf meshes resolve the carve (half deepest-leaf pad on corridors
 *     keeps chunk borders on the same boost → no T-junction stripes).
 *  3. **Stamp sampler** — {@link applyHeightBrush} or segmented corridor
 *     (`forEachTexelInAabb` + raise/lower passes). Same ±1 texel margin.
 *  4. **Remesh / collider** — {@link rebuildTerrainDerivatives} (meshDirty +
 *     Rapier heightfields + BVH + ground-mutation callbacks).
 *
 * Ribbon / water meshes sample the **analytic** sampler after carve — never
 * mesh-catchup onto LOD geometry.
 */

import { applyOverride, type DensityMap, type WorldAabb } from './density-map';
import { forEachCorridorSegment, segmentAabb } from './corridor';

/**
 * Half the deepest quadtree leaf size (m). Pad density corridors by this so
 * neighbouring chunks that share a border both see the boost.
 */
export function densityLeafPad(worldSize: number, levels: number): number {
  const lv = Math.max(1, levels);
  return worldSize / 2 ** Math.max(0, lv - 1) / 2;
}

/**
 * Stamp density along a polyline, per segment. Optional `leafPad` expands
 * reach so chunk borders sharing the corridor get the same boost.
 */
export function applyCorridorDensity(
  density: DensityMap,
  path: number[],
  reach: number,
  boost: number,
  leafPad = 0
): void {
  if (path.length < 4 || !(reach > 0)) return;
  const r = reach + Math.max(0, leafPad);
  forEachCorridorSegment(path, (ax, az, bx, bz) => {
    applyOverride(density, segmentAabb(ax, az, bx, bz, r), boost);
  });
}

/**
 * Stamp density for a compact feature (lake bowl, settlement pad). Optional
 * `leafPad` expands the AABB the same way corridors do.
 */
export function applyFeatureDensity(
  density: DensityMap,
  aabb: WorldAabb,
  boost: number,
  leafPad = 0
): void {
  const pad = Math.max(0, leafPad);
  if (pad <= 0) {
    applyOverride(density, aabb, boost);
    return;
  }
  applyOverride(
    density,
    {
      minX: aabb.minX - pad,
      maxX: aabb.maxX + pad,
      minZ: aabb.minZ - pad,
      maxZ: aabb.maxZ + pad,
    },
    boost
  );
}
