/**
 * Snap bridge deck XZ to the river centreline under the span — not the
 * midpoint of authored Ways (those can sit asymmetric about the channel).
 */
import type { State } from '../../core';
import { nearestOnPolyline } from '../terrain/corridor';
import {
  getRiverFlatPath,
  getWaterBodies,
  type RiverWaterBody,
} from '../water/registry';
import { bridgeMidXZ, pathArcLength } from './bridge';

export interface RiverCrossing {
  /** World XZ on the river centreline under the span. */
  x: number;
  z: number;
  /** Half-width of the waterline (m). */
  half: number;
  /** Half-width of the full carve / water-mesh ribbon (m). */
  carveHalf: number;
}

/**
 * Nearest river centreline point to the span midpoint, when the span actually
 * crosses that river's carve footprint.
 */
export function riverCrossingAt(
  state: State,
  path: number[]
): RiverCrossing | null {
  if (path.length < 4) return null;
  const mid = bridgeMidXZ(path);
  const spanHalf = pathArcLength(path) * 0.5;
  let best: RiverCrossing | null = null;
  let bestDist = Infinity;

  for (const body of getWaterBodies(state)) {
    if (body.kind !== 'river') continue;
    const crossing = crossingOnRiver(body, mid.x, mid.z, spanHalf);
    if (!crossing) continue;
    const d = Math.hypot(crossing.x - mid.x, crossing.z - mid.z);
    if (d < bestDist) {
      bestDist = d;
      best = crossing;
    }
  }
  return best;
}

/** Pure helper — river body + seed XZ → centreline hit, or null if too far. */
export function crossingOnRiver(
  body: RiverWaterBody,
  seedX: number,
  seedZ: number,
  spanHalf: number
): RiverCrossing | null {
  const flat = getRiverFlatPath(body.path);
  if (flat.length < 4) return null;
  const n = nearestOnPolyline(flat, seedX, seedZ);
  if (!n) return null;
  const half = (body.shoreWidth ?? body.width) * 0.5;
  const carveHalf = (body.carveWidth ?? body.width * 1.5) * 0.5;
  // Span must reach the carve — otherwise this river is a neighbour, not ours.
  if (n.dist > carveHalf + Math.max(spanHalf, half)) return null;
  return { x: n.cx, z: n.cz, half, carveHalf };
}

/**
 * Deck spawn/seat XZ: river centreline when available, else Ways midpoint.
 */
export function bridgeDeckCenterXZ(
  state: State,
  path: number[]
): { x: number; z: number } {
  return riverCrossingAt(state, path) ?? bridgeMidXZ(path);
}
