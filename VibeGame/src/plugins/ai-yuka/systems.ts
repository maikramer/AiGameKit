import { defineSystem, defineQuery } from '../../core';
import type { State, System } from '../../core';
import { Transform } from '../transforms/components';
import { Health } from '../combat/components';
import { YukaAgentComponent } from './components';
import { FactionComponent } from '../combat/components';
import { YUKA_BEHAVIOR_FLOCK, YUKA_BEHAVIOR_SEPARATION } from './components';
import {
  createYukaRuntime,
  syncVehicleFromTransform,
  applyBehaviorMask,
  bindTarget,
  emitNavTarget,
  TargetProxy,
} from './vehicle-bridge';
import { getYukaRuntimeMap, deleteYukaRuntime } from './context';
import type { YukaRuntime } from './context';
import type { Vehicle } from 'yuka';

const agentQuery = defineQuery([YukaAgentComponent, Transform]);

/** Neighbors considered for separation/flock. Smaller = cheaper, tighter packs. */
const NEIGHBOR_RADIUS = 4;

/**
 * The yuka agent system. Each frame, for every active {@link YukaAgentComponent}
 * entity it:
 *   1. Ensures a {@link YukaRuntime} (lazily, per-State side table).
 *   2. Syncs the yuka Vehicle position from Transform.
 *   3. Populates `vehicle.neighbors` from same-faction allies (for sep/flock).
 *   4. Rebinds the target proxy to the focus entity / static target.
 *   5. Applies the behavior bitmask.
 *   6. Runs `vehicle.update(dt)` and forwards the goal to the navmesh crowd.
 *
 * It never writes `Transform.posX/Z` directly — that is the crowd's job. This
 * is what prevents the position-fighting that made the old lunges jittery.
 */
export const YukaAgentSystem: System = defineSystem({
  name: 'YukaAgentSystem',
  group: 'simulation',
  update(state: State): void {
    const dt = state.time.deltaTime;
    const runtimes = getYukaRuntimeMap(state);
    const agents = agentQuery(state.world);

    // First pass: GC dead/inactive entities so their neighbors are not counted.
    for (let i = 0; i < agents.length; i++) {
      const eid = agents[i];
      if (YukaAgentComponent.active[eid] === 0) continue;
      if (Health.current[eid] <= 0) {
        const rt = runtimes.get(eid);
        if (rt) deleteYukaRuntime(state, eid);
        continue;
      }
      // Lazily create runtime.
      if (!runtimes.has(eid)) runtimes.set(eid, createYukaRuntime());
    }

    // Resolve the shared hero proxy once per frame (pursuit/evade target).
    // The "focus" target for an agent is YukaAgentComponent.targetEid; when it
    // points at the hero we bind the same proxy for every agent that frame.
    // We keep one proxy per (State, targetEid) to avoid per-agent allocation.
    const proxyByEid = new Map<number, TargetProxy>();

    for (let i = 0; i < agents.length; i++) {
      const eid = agents[i];
      if (YukaAgentComponent.active[eid] === 0) continue;
      const rt = runtimes.get(eid);
      if (!rt) continue;
      if (Health.current[eid] <= 0) continue;

      stepAgent(state, eid, rt, dt, runtimes, proxyByEid);
    }
  },
});

const _tmpNeighbors: Vehicle[] = [];
/** Reused per frame for the static-target path (no per-agent allocation). */
let _staticProxy: TargetProxy | null = null;

function stepAgent(
  state: State,
  eid: number,
  rt: YukaRuntime,
  dt: number,
  runtimes: Map<number, YukaRuntime>,
  proxyByEid: Map<number, TargetProxy>
): void {
  syncVehicleFromTransform(rt, eid);
  rt.vehicle.maxSpeed = YukaAgentComponent.maxSpeed[eid] || 3;
  rt.vehicle.maxForce = YukaAgentComponent.maxForce[eid] || 8;

  // Neighbors for separation/flock: same-faction, alive, within radius.
  const needsNeighbors =
    (YukaAgentComponent.behavior[eid] &
      (YUKA_BEHAVIOR_SEPARATION | YUKA_BEHAVIOR_FLOCK)) !==
    0;
  if (needsNeighbors) {
    populateNeighbors(state, eid, rt, runtimes);
  } else if (rt.vehicle.neighbors.length !== 0) {
    rt.vehicle.neighbors.length = 0;
  }

  // Resolve + bind target (hero proxy or static point).
  const focusEid = YukaAgentComponent.targetEid[eid];
  if (focusEid > 0 && Health.current[focusEid] > 0) {
    let proxy = proxyByEid.get(focusEid);
    if (!proxy) {
      proxy = new TargetProxy();
      proxyByEid.set(focusEid, proxy);
    }
    proxy.position.set(
      Transform.posX[focusEid],
      Transform.posY[focusEid],
      Transform.posZ[focusEid]
    );
    bindTarget(rt, proxy);
  } else {
    // Static target → reuse a per-frame static proxy positioned at targetX/Z.
    // (bindTarget copies the proxy position into arrive/flee Vector3 targets.)
    if (!_staticProxy) _staticProxy = new TargetProxy();
    _staticProxy.position.set(
      YukaAgentComponent.targetX[eid],
      Transform.posY[eid],
      YukaAgentComponent.targetZ[eid]
    );
    bindTarget(rt, _staticProxy);
  }

  applyBehaviorMask(rt, YukaAgentComponent.behavior[eid]);
  emitNavTarget(state, rt, eid, dt);
}

/**
 * Fill `vehicle.neighbors` with the same-faction allies' vehicles within
 * {@link NEIGHBOR_RADIUS}. Reuses the agents' own runtimes so we never allocate
 * yuka objects here (we borrow their `vehicle` references).
 */
function populateNeighbors(
  state: State,
  eid: number,
  rt: YukaRuntime,
  runtimes: Map<number, YukaRuntime>
): void {
  const myFaction = FactionComponent.tag[eid];
  const x = Transform.posX[eid];
  const z = Transform.posZ[eid];
  const r2 = NEIGHBOR_RADIUS * NEIGHBOR_RADIUS;
  _tmpNeighbors.length = 0;
  const all = agentQuery(state.world);
  for (let i = 0; i < all.length; i++) {
    const other = all[i];
    if (other === eid) continue;
    if (YukaAgentComponent.active[other] === 0) continue;
    if (Health.current[other] <= 0) continue;
    if (FactionComponent.tag[other] !== myFaction) continue;
    const dx = Transform.posX[other] - x;
    const dz = Transform.posZ[other] - z;
    if (dx * dx + dz * dz > r2) continue;
    const ort = runtimes.get(other);
    if (ort) _tmpNeighbors.push(ort.vehicle);
  }
  // yuka's `neighbors` is a read-only array reference; clear + refill in place.
  rt.vehicle.neighbors.length = 0;
  for (let i = 0; i < _tmpNeighbors.length; i++) {
    rt.vehicle.neighbors.push(_tmpNeighbors[i]);
  }
  rt.vehicle.neighborhoodRadius = NEIGHBOR_RADIUS;
}
