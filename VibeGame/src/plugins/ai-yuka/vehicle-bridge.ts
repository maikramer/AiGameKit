import { Vehicle } from 'yuka';
import {
  AlignmentBehavior,
  ArriveBehavior,
  CohesionBehavior,
  EvadeBehavior,
  FleeBehavior,
  PursuitBehavior,
  SeekBehavior,
  SeparationBehavior,
  WanderBehavior,
} from 'yuka';
import type { State } from '../../core';
import { Transform } from '../transforms/components';
import { NavMeshAgent } from '../navmesh/components';
import { setAgentTarget } from '../navmesh';
import {
  YUKA_BEHAVIOR_ARRIVE,
  YUKA_BEHAVIOR_EVADE,
  YUKA_BEHAVIOR_FLEE,
  YUKA_BEHAVIOR_SEEK,
  YUKA_BEHAVIOR_PURSUIT,
  YUKA_BEHAVIOR_WANDER,
  YUKA_BEHAVIOR_SEPARATION,
  YUKA_BEHAVIOR_FLOCK,
} from './components';
import type { YukaRuntime } from './context';
import type { SteeringBehaviorId } from './context';

/**
 * A lightweight stand-in "target vehicle" that yuka pursuit/evade need. The
 * real target entity (the hero) is not a yuka Vehicle, so we sync its Transform
 * into this object's position each frame before the yuka behaviors read it.
 */
export class TargetProxy extends Vehicle {
  // No-op: only `.position` (and `.velocity`, optionally) is read by pursuit/
  // evade. Update orientation is irrelevant for a read-only target.
}

// ── Behavior factory: instantiate one of each, keep them inert until toggled ──

function createBehaviors(): Map<SteeringBehaviorId, unknown> {
  const map = new Map<SteeringBehaviorId, unknown>();
  map.set('seek', new SeekBehavior());
  map.set('arrive', new ArriveBehavior());
  map.set('pursuit', new PursuitBehavior());
  map.set('evade', new EvadeBehavior());
  map.set('flee', new FleeBehavior());
  map.set('wander', new WanderBehavior());
  map.set('separation', new SeparationBehavior());
  map.set('alignment', new AlignmentBehavior());
  map.set('cohesion', new CohesionBehavior());
  return map;
}

/**
 * Build the runtime for a fresh yuka agent: a Vehicle with all steering
 * behaviors attached (but all inactive) plus a reusable target proxy. Behaviors
 * are toggled via `.active` in {@link applyBehaviorMask}.
 */
export function createYukaRuntime(): YukaRuntime {
  const vehicle = new Vehicle();
  vehicle.maxSpeed = 3;
  vehicle.maxForce = 8;
  vehicle.updateOrientation = false; // we own heading via Transform.eulerY
  vehicle.updateNeighborhood = false; // we populate .neighbors manually

  const behaviors = createBehaviors();
  for (const b of behaviors.values()) {
    vehicle.steering.add(b as never);
  }
  // All behaviors start inactive; the system flips the ones in the mask.
  for (const b of behaviors.values()) {
    (b as { active: boolean }).active = false;
  }

  return {
    vehicle,
    behaviors: behaviors as YukaRuntime['behaviors'],
    lastMask: -1,
    lastTargetEid: 0,
  };
}

/** Sync the yuka Vehicle's position from the entity's Transform (planar only). */
export function syncVehicleFromTransform(
  runtime: YukaRuntime,
  eid: number
): void {
  const v = runtime.vehicle;
  v.position.set(Transform.posX[eid], Transform.posY[eid], Transform.posZ[eid]);
}

/**
 * Activate exactly the steering behaviors set in `mask`. Idempotent: skips work
 * when the mask has not changed since the last call. Target binding (pursuit/
 * arrive/flee/evade) is handled separately in {@link bindTarget}.
 */
export function applyBehaviorMask(runtime: YukaRuntime, mask: number): void {
  if (mask === runtime.lastMask) return;
  runtime.lastMask = mask;
  const b = runtime.behaviors;
  set(b, 'seek', mask, YUKA_BEHAVIOR_SEEK);
  set(b, 'arrive', mask, YUKA_BEHAVIOR_ARRIVE);
  set(b, 'pursuit', mask, YUKA_BEHAVIOR_PURSUIT);
  set(b, 'evade', mask, YUKA_BEHAVIOR_EVADE);
  set(b, 'flee', mask, YUKA_BEHAVIOR_FLEE);
  set(b, 'wander', mask, YUKA_BEHAVIOR_WANDER);
  // Separation is its own flag OR implied by FLOCK (flocking = sep+align+coh).
  const sep =
    (mask & YUKA_BEHAVIOR_SEPARATION) !== 0 ||
    (mask & YUKA_BEHAVIOR_FLOCK) !== 0;
  toggle(b.get('separation'), sep);
  toggle(b.get('alignment'), (mask & YUKA_BEHAVIOR_FLOCK) !== 0);
  toggle(b.get('cohesion'), (mask & YUKA_BEHAVIOR_FLOCK) !== 0);
}

function set(
  b: YukaRuntime['behaviors'],
  id: SteeringBehaviorId,
  mask: number,
  flag: number
): void {
  toggle(b.get(id), (mask & flag) !== 0);
}

function toggle(behavior: unknown, on: boolean): void {
  if (behavior) (behavior as { active: boolean }).active = on;
}

/**
 * Point pursuit/evade at the target proxy (the hero) and copy its position into
 * the arrive/flee target vectors. Only rebinds the Vehicle references when the
 * target changed — yuka caches them, so this avoids re-pointing every frame.
 *
 * Note the yuka field names: `PursuitBehavior.evader` (the prey) and
 * `EvadeBehavior.pursuer` (the thing chasing us) — both take a Vehicle.
 * `ArriveBehavior.target` and `FleeBehavior.target` are plain Vector3s.
 */
export function bindTarget(runtime: YukaRuntime, target: TargetProxy): void {
  const pursuit = runtime.behaviors.get('pursuit') as
    PursuitBehavior | undefined;
  const evade = runtime.behaviors.get('evade') as
    (EvadeBehavior & { pursuer?: Vehicle }) | undefined;
  const seek = runtime.behaviors.get('seek') as
    (SeekBehavior & { target: { copy(p: unknown): void } }) | undefined;
  const arrive = runtime.behaviors.get('arrive') as
    (ArriveBehavior & { target: { copy(p: unknown): void } }) | undefined;
  const flee = runtime.behaviors.get('flee') as
    (FleeBehavior & { target: { copy(p: unknown): void } }) | undefined;
  if (pursuit && pursuit.evader !== target) pursuit.evader = target;
  if (evade && evade.pursuer !== target) evade.pursuer = target;
  if (seek) seek.target.copy(target.position);
  if (arrive) arrive.target.copy(target.position);
  if (flee) flee.target.copy(target.position);
}

/**
 * After `vehicle.update(dt)`, push the computed planar goal into the navmesh
 * crowd agent as a *target point* (not a raw position write). This is the key
 * seam that avoids the jitter bug: the crowd, not yuka, owns the entity's
 * position on the navmesh. Yuka only chooses *where to go*; recast resolves the
 * path and the writeback to Transform.
 *
 * Returns true if a target was emitted (so the caller can clear it otherwise).
 */
export function emitNavTarget(
  state: State,
  runtime: YukaRuntime,
  eid: number,
  dt: number
): boolean {
  // Run yuka integration: accumulates steering → velocity → position delta.
  runtime.vehicle.update(dt);
  const goal = runtime.vehicle.position;
  // Only feed the crowd when it's actually driving this entity. When navmesh is
  // absent or the agent was disabled (e.g. mid-lunge), the caller owns Transform.
  if (NavMeshAgent.agentIndex[eid] === -1 || NavMeshAgent.enabled[eid] === 0) {
    return false;
  }
  setAgentTarget(state, eid, goal.x, Transform.posY[eid], goal.z);
  return true;
}
