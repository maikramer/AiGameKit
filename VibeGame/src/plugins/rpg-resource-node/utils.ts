import type { EnumMapping, State } from '../../core';
import { emitEvent } from '../rpg-core/events';
import { ResourceNode } from './components';

/** Emitted when a node is harvested (yield > 0). Payload: {@link NodeHarvestedPayload}. */
export const NODE_HARVESTED = 'node:harvested';
/** Emitted when a depleted node's respawn timer elapses. Payload: {@link NodeRespawnedPayload}. */
export const NODE_RESPAWNED = 'node:respawned';

export interface NodeHarvestedPayload {
  readonly target: number;
  readonly kind: string;
  readonly yield: number;
  readonly depleted: boolean;
}

export interface NodeRespawnedPayload {
  readonly target: number;
  readonly kind: string;
}

/** Config enum key used for kind resolution: `config.enums['resource-node'].kind`. */
export const RESOURCE_NODE_COMPONENT = 'resource-node';
export const RESOURCE_NODE_KIND_FIELD = 'kind';

/**
 * Resolve a kind string (e.g. `"stone"`) to its numeric enum value using the
 * `resource-node.kind` config enum. Numeric strings pass through. Unknown kinds
 * fall back to `0` so a typo never crashes the simulation.
 *
 * Extend the enum by registering more entries on the plugin config
 * (`enums['resource-node'].kind = { wood:0, stone:1, ore:2, crystal:3, ... }`).
 */
export function resolveResourceNodeKind(state: State, value: string): number {
  const asNum = Number(value);
  if (Number.isFinite(asNum) && /^\s*-?\d+(\.\d+)?\s*$/.test(value)) {
    return Math.trunc(asNum);
  }
  const mapping = getKindMapping(state);
  const key = value.toLowerCase();
  if (mapping && key in mapping) {
    return mapping[key];
  }
  return 0;
}

/** Reverse-lookup the kind enum value to its string name (e.g. `1` → `"stone"`). */
export function kindToString(state: State, kindValue: number): string {
  const mapping = getKindMapping(state);
  if (mapping) {
    for (const [name, num] of Object.entries(mapping)) {
      if (num === kindValue) return name;
    }
  }
  return String(kindValue);
}

function getKindMapping(state: State): EnumMapping | undefined {
  const enums = state.config.getEnums(RESOURCE_NODE_COMPONENT);
  return enums?.[RESOURCE_NODE_KIND_FIELD];
}

export function isResourceNode(state: State, eid: number): boolean {
  return state.hasComponent(eid, ResourceNode);
}

export function getResourceNodeKind(state: State, eid: number): string {
  if (!state.hasComponent(eid, ResourceNode)) return '';
  return kindToString(state, ResourceNode.kind[eid]);
}

export function isDepleted(state: State, eid: number): boolean {
  if (!state.hasComponent(eid, ResourceNode)) return false;
  return ResourceNode.depleted[eid] !== 0;
}

/**
 * Harvest a resource node: returns the yield amount and emits
 * {@link NODE_HARVESTED}. Respawnable nodes (`respawn > 0`) are marked
 * depleted and scheduled to respawn; one-shot nodes (`respawn == 0`) are left
 * untouched (the caller removes them). Harvesting an already-depleted node
 * returns `0` and emits nothing.
 */
export function harvest(state: State, eid: number): number {
  if (!state.hasComponent(eid, ResourceNode)) return 0;
  if (ResourceNode.depleted[eid] !== 0) return 0;

  const amount = ResourceNode.yield[eid];
  const kindName = kindToString(state, ResourceNode.kind[eid]);
  const respawn = ResourceNode.respawn[eid];
  let depleted = false;

  if (respawn > 0) {
    ResourceNode.depleted[eid] = 1;
    ResourceNode.respawnAt[eid] = state.time.elapsed + respawn;
    depleted = true;
  }

  emitEvent(state, NODE_HARVESTED, {
    target: eid,
    kind: kindName,
    yield: amount,
    depleted,
  } satisfies NodeHarvestedPayload);

  return amount;
}
