import type { State } from '../../core';
import type { Vehicle } from 'yuka';
import type { SteeringBehavior } from 'yuka';

/**
 * Per-entity rich runtime kept off the ECS arrays, mirroring the side-table
 * pattern used by `rpg-ai` (config/instance) and `ai-steering` (SteeringRow).
 * The {@link Vehicle} is a yuka object (not queryable/serializable), so it
 * cannot live in a typed-array component.
 */
export interface YukaRuntime {
  /** The yuka vehicle this entity drives. */
  vehicle: Vehicle;
  /** Steering behaviors instantiated once, toggled via `.active` per frame. */
  behaviors: Map<SteeringBehaviorId, SteeringBehavior>;
  /** Last behavior mask applied — avoids re-touching the manager every frame. */
  lastMask: number;
  /** Cached to detect target switches and rebind pursuit/arrive targets. */
  lastTargetEid: number;
}

export type SteeringBehaviorId =
  | 'seek'
  | 'arrive'
  | 'pursuit'
  | 'evade'
  | 'flee'
  | 'wander'
  | 'separation'
  | 'alignment'
  | 'cohesion';

const runtimesByState = new WeakMap<State, Map<number, YukaRuntime>>();

/**
 * Get (or lazily create) the per-`State` map of eid → runtime. Stored in a
 * WeakMap so destroying a game / GC'ing the State drops all vehicles.
 */
export function getYukaRuntimeMap(state: State): Map<number, YukaRuntime> {
  let m = runtimesByState.get(state);
  if (!m) {
    m = new Map();
    runtimesByState.set(state, m);
  }
  return m;
}

export function getYukaRuntime(
  state: State,
  eid: number
): YukaRuntime | undefined {
  return runtimesByState.get(state)?.get(eid);
}

export function deleteYukaRuntime(state: State, eid: number): void {
  runtimesByState.get(state)?.delete(eid);
}
