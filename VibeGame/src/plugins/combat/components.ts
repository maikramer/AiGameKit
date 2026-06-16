import { MAX_ENTITIES } from '../../core/ecs/constants';
import type { State } from '../../core';
import {
  COMBAT_DAMAGED,
  COMBAT_HEALED,
  COMBAT_KILLED,
  emitEvent,
} from '../rpg-core/events';
import { getDataRegistry } from '../rpg-core/registry';

export const Health = {
  current: new Float32Array(MAX_ENTITIES),
  max: new Float32Array(MAX_ENTITIES),
} as const;

export const ProjectileData = {
  damage: new Float32Array(MAX_ENTITIES),
  ownerEid: new Int32Array(MAX_ENTITIES),
  lifetime: new Float32Array(MAX_ENTITIES),
  age: new Float32Array(MAX_ENTITIES),
} as const;

// `maxLife` is the authoritative lifetime for `spawnProjectile` entities;
// `ProjectileCleanupSystem` prefers it over the legacy `ProjectileData.lifetime`.
export const ProjectileConfig = {
  speed: new Float32Array(MAX_ENTITIES),
  maxLife: new Float32Array(MAX_ENTITIES),
  damage: new Float32Array(MAX_ENTITIES),
  faction: new Uint8Array(MAX_ENTITIES),
} as const;

export const FactionComponent = {
  tag: new Uint8Array(MAX_ENTITIES),
} as const;

export const FACTION_TAG_NAMES: string[] = [
  'player',
  'enemy',
  'neutral',
  'merchant',
];

const FACTION_TAG_IDS: Map<string, number> = new Map(
  FACTION_TAG_NAMES.map((name, id) => [name, id])
);

/**
 * Registry data contract for kind `faction-hostility`. `isHostile` checks
 * membership symmetrically: either ordering of a pair counts as hostile.
 */
export interface FactionHostilityMatrix {
  readonly pairs: ReadonlyArray<readonly [string, string]>;
}

// Bound by bindCombatState so the stateless damageHealth/healHealth helpers can
// emit EventBus events without taking a State param (signature-preserving).
let activeState: State | null = null;

export function bindCombatState(state: State): void {
  activeState = state;
}

const deathFlagsByState = new WeakMap<State, Uint8Array>();

export function getDeathFlags(state: State): Uint8Array {
  let flags = deathFlagsByState.get(state);
  if (!flags) {
    flags = new Uint8Array(MAX_ENTITIES);
    deathFlagsByState.set(state, flags);
  }
  return flags;
}

export function damageHealth(eid: number, amount: number): void {
  const current = Health.current[eid];
  if (current <= 0) return;
  const newHp = Math.max(0, current - amount);
  Health.current[eid] = newHp;
  if (!activeState) return;
  emitEvent(activeState, COMBAT_DAMAGED, { target: eid, amount, newHp });
  if (newHp <= 0) {
    emitEvent(activeState, COMBAT_KILLED, { target: eid });
  }
}

export function healHealth(eid: number, amount: number): void {
  const newHp = Math.min(Health.max[eid], Health.current[eid] + amount);
  Health.current[eid] = newHp;
  if (activeState && newHp > 0) {
    getDeathFlags(activeState)[eid] = 0;
  }
  if (!activeState) return;
  emitEvent(activeState, COMBAT_HEALED, { target: eid, amount, newHp });
}

export function isAlive(eid: number): boolean {
  return Health.current[eid] > 0;
}

export function isDead(eid: number): boolean {
  return Health.current[eid] <= 0;
}

export function setMaxHealth(eid: number, max: number): void {
  Health.max[eid] = max;
  Health.current[eid] = max;
  if (activeState) {
    getDeathFlags(activeState)[eid] = 0;
  }
}

export function setProjectileOwner(eid: number, ownerEid: number): void {
  ProjectileData.ownerEid[eid] = ownerEid;
}

export function incrementProjectileAge(eid: number, dt: number): void {
  ProjectileData.age[eid] += dt;
}

export function isProjectileExpired(eid: number): boolean {
  return ProjectileData.age[eid] >= ProjectileData.lifetime[eid];
}

export function getFaction(state: State, eid: number): string {
  void state;
  const id = FactionComponent.tag[eid];
  return FACTION_TAG_NAMES[id] ?? `unknown:${id}`;
}

export function setFaction(state: State, eid: number, tag: string): void {
  void state;
  let id = FACTION_TAG_IDS.get(tag);
  if (id === undefined) {
    id = FACTION_TAG_NAMES.length;
    if (id > 255) {
      throw new Error(
        `Faction tag overflow: cannot register more than 256 factions`
      );
    }
    FACTION_TAG_IDS.set(tag, id);
    FACTION_TAG_NAMES.push(tag);
  }
  FactionComponent.tag[eid] = id;
}

export function isHostile(state: State, a: number, b: number): boolean {
  const matrix = getDataRegistry(state).get<FactionHostilityMatrix>(
    'faction-hostility',
    'default'
  );
  if (!matrix || !matrix.pairs) return false;
  const tagA = getFaction(state, a);
  const tagB = getFaction(state, b);
  for (const [x, y] of matrix.pairs) {
    if ((x === tagA && y === tagB) || (x === tagB && y === tagA)) return true;
  }
  return false;
}
