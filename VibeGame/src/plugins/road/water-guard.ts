import type { State } from '../../core';
import {
  getRiverFlatPath,
  getWaterBodies,
  type WaterBody,
} from '../water/registry';
import { distanceToPath } from '../water/path-utils';
import { nearestOnPolyline } from '../terrain/corridor';

export interface WaterPreserveZones {
  /** Field-local Y: road may cut but must not fill below this. */
  noRaiseBelowY?: number;
  /** Lake carve discs in field-local XZ — road stamp skips entirely. */
  discs: Array<{ x: number; z: number; r: number }>;
  /** River carve ribbons in field-local XZ. */
  ribbons: Array<{ path: number[]; half: number }>;
}

/**
 * Build road×water guards for a corridor stamp.
 *
 * `RoadApply` runs after water carves. Corridor `blend` otherwise re-fills
 * bowls (leak) or cuts a rectangular shelf into the shore (coarse texel +
 * wide artery falloff). Returns raise-floor + footprints to leave untouched.
 */
export function waterPreserveZonesLocal(
  state: State,
  localPath: number[],
  reach: number,
  worldOffset: { x: number; z: number },
  baseY: number
): WaterPreserveZones {
  const empty: WaterPreserveZones = { discs: [], ribbons: [] };
  if (localPath.length < 4 || !(reach >= 0)) return empty;
  const bodies = getWaterBodies(state);
  if (bodies.length === 0) return empty;

  let floor = Infinity;
  const discs: WaterPreserveZones['discs'] = [];
  const ribbons: WaterPreserveZones['ribbons'] = [];

  for (const body of bodies) {
    if (!corridorOverlapsWater(localPath, reach, body, worldOffset)) continue;
    const localY = body.waterY - baseY;
    if (localY < floor) floor = localY;
    if (body.kind === 'lake') {
      // Waterline disc only — beach/banks stay stampable so arteries can
      // re-terrace pad tips after the river nibble (bridge abutments).
      discs.push({
        x: body.x - worldOffset.x,
        z: body.z - worldOffset.z,
        r: body.shoreRadius > 0 ? body.shoreRadius : body.radius * 0.85,
      });
    } else {
      const localRiver: number[] = [];
      for (const [wx, wz] of body.path) {
        localRiver.push(wx - worldOffset.x, wz - worldOffset.z);
      }
      const shoreHalf = (body.shoreWidth ?? body.width * 0.95) * 0.5;
      ribbons.push({
        path: localRiver,
        half: shoreHalf,
      });
    }
  }

  return {
    noRaiseBelowY: Number.isFinite(floor) ? floor : undefined,
    discs,
    ribbons,
  };
}

/**
 * Field-local Y floor for road stamps that overlap a lake/river carved earlier.
 * @deprecated Prefer {@link waterPreserveZonesLocal} (also skips carve discs).
 */
export function waterNoRaiseFloorLocal(
  state: State,
  localPath: number[],
  reach: number,
  worldOffset: { x: number; z: number },
  baseY: number
): number | undefined {
  return waterPreserveZonesLocal(state, localPath, reach, worldOffset, baseY)
    .noRaiseBelowY;
}

/** True when the road corridor footprint touches a water carve. */
export function corridorOverlapsWater(
  localPath: number[],
  reach: number,
  body: WaterBody,
  worldOffset: { x: number; z: number }
): boolean {
  const ox = worldOffset.x;
  const oz = worldOffset.z;
  if (body.kind === 'lake') {
    const lx = body.x - ox;
    const lz = body.z - oz;
    const r = (body.carveRadius ?? body.radius) + reach;
    const n = nearestOnPolyline(localPath, lx, lz);
    if (n && n.dist <= r) return true;
    // Degenerate / single-sample paths: also test vertices.
    for (let i = 0; i < localPath.length; i += 2) {
      if (Math.hypot(localPath[i]! - lx, localPath[i + 1]! - lz) <= r) {
        return true;
      }
    }
    return false;
  }

  const half = (body.carveWidth ?? body.width) * 0.5 + reach;
  const flat = getRiverFlatPath(body.path);
  // Sample along the road (not only vertices) — a crossing often sits mid-segment.
  const step = Math.max(1, half * 0.5);
  for (let i = 0; i + 3 < localPath.length; i += 2) {
    const ax = localPath[i]!;
    const az = localPath[i + 1]!;
    const bx = localPath[i + 2]!;
    const bz = localPath[i + 3]!;
    const len = Math.hypot(bx - ax, bz - az);
    const n = Math.max(1, Math.ceil(len / step));
    for (let s = 0; s <= n; s++) {
      const t = s / n;
      const wx = ax + (bx - ax) * t + ox;
      const wz = az + (bz - az) * t + oz;
      if (distanceToPath(flat, wx, wz) <= half) return true;
    }
  }
  return false;
}
