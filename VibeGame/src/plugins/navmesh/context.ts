import { Pathfinding } from 'three-pathfinding';
import * as THREE from 'three';
import type { State } from '../../core';

export interface NavmeshContext {
  pathfinding: Pathfinding;
  zoneId: string;
  defaultGroup: number;
  /** waypoints por entidade nav-agent */
  waypoints: Map<number, THREE.Vector3[]>;
  waypointIndex: Map<number, number>;
  zoneRegistered: boolean;
}

const stateToNav = new WeakMap<State, NavmeshContext>();

export function getNavmeshContext(state: State): NavmeshContext {
  let ctx = stateToNav.get(state);
  if (!ctx) {
    const pathfinding = new Pathfinding();
    ctx = {
      pathfinding,
      zoneId: 'main',
      defaultGroup: 0,
      waypoints: new Map(),
      waypointIndex: new Map(),
      zoneRegistered: false,
    };
    stateToNav.set(state, ctx);
  }
  return ctx;
}

export function setNavmeshZone(state: State, zoneId: string): void {
  getNavmeshContext(state).zoneId = zoneId;
}
