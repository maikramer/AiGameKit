import { defineQuery, type State } from '../../core';
import { isPointOnRoad } from '../terrain/brush-registry';
import { getRoadData, Road } from './components';
import { distanceToPolyline } from './geometry';
import type { RoadNetworkGraph } from './network';
import { pathBetweenWays } from './network';

const roadQuery = defineQuery([Road]);

const NETWORK_GRAPHS = new WeakMap<State, Map<number, RoadNetworkGraph>>();

/** Store graph produced by a `<RoadNetwork>` entity (for pathTo / analyze). */
export function setRoadNetworkGraph(
  state: State,
  networkEid: number,
  graph: RoadNetworkGraph
): void {
  let m = NETWORK_GRAPHS.get(state);
  if (!m) {
    m = new Map();
    NETWORK_GRAPHS.set(state, m);
  }
  m.set(networkEid, graph);
}

export function getRoadNetworkGraphs(state: State): RoadNetworkGraph[] {
  const m = NETWORK_GRAPHS.get(state);
  return m ? [...m.values()] : [];
}

export function clearRoadNetworkGraphs(state: State): void {
  NETWORK_GRAPHS.delete(state);
}

/** True if (x,z) sits on any registered road brush corridor. */
export function onRoad(
  state: State,
  x: number,
  z: number,
  _slackMeters?: number
): boolean {
  return isPointOnRoad(state, x, z);
}

export type NearestRoadHit = {
  eid: number;
  x: number;
  z: number;
  distance: number;
  halfWidth: number;
};

/**
 * Closest point on any authored road centerline (pre-smooth path).
 * Useful for AI snap / compass.
 */
export function nearestRoad(
  state: State,
  x: number,
  z: number
): NearestRoadHit | null {
  let best: NearestRoadHit | null = null;
  for (const eid of roadQuery(state.world)) {
    const data = getRoadData(state, eid);
    if (!data || data.path.length < 4) continue;
    const half = (Road.width[eid] || 2) / 2;
    // Sample distance to polyline; recover closest point by segment walk.
    const d = distanceToPolyline(data.path, x, z);
    let cx = data.path[0]!;
    let cz = data.path[1]!;
    let bestD2 = Infinity;
    for (let i = 0; i + 3 < data.path.length; i += 2) {
      const ax = data.path[i]!;
      const az = data.path[i + 1]!;
      const bx = data.path[i + 2]!;
      const bz = data.path[i + 3]!;
      const dx = bx - ax;
      const dz = bz - az;
      const lenSq = dx * dx + dz * dz;
      let t = lenSq > 0 ? ((x - ax) * dx + (z - az) * dz) / lenSq : 0;
      t = t < 0 ? 0 : t > 1 ? 1 : t;
      const px = ax + t * dx;
      const pz = az + t * dz;
      const d2 = (px - x) ** 2 + (pz - z) ** 2;
      if (d2 < bestD2) {
        bestD2 = d2;
        cx = px;
        cz = pz;
      }
    }
    if (!best || d < best.distance) {
      best = { eid, x: cx, z: cz, distance: d, halfWidth: half };
    }
  }
  return best;
}

/**
 * Shortest Way-id path across any stored RoadNetwork graph.
 * Returns null if either id missing or unreachable.
 */
export function pathToWay(
  state: State,
  fromWayId: string,
  toWayId: string
): string[] | null {
  for (const g of getRoadNetworkGraphs(state)) {
    const p = pathBetweenWays(g, fromWayId, toWayId);
    if (p) return p;
  }
  return null;
}

/** World XZ polyline following Way centers along a pathToWay result. */
export function wayPathPolyline(
  state: State,
  wayIds: string[]
): number[] | null {
  if (wayIds.length < 2) return null;
  for (const g of getRoadNetworkGraphs(state)) {
    const pts: number[] = [];
    let ok = true;
    for (const id of wayIds) {
      const w = g.ways.get(id);
      if (!w) {
        ok = false;
        break;
      }
      pts.push(w.x, w.z);
    }
    if (ok) return pts;
  }
  return null;
}
