import {
  defineComponent,
  F32,
  I32,
  U8,
} from '../../core/ecs/component-storage';
import { MAX_ENTITIES } from '../../core/ecs/constants';
import type { State } from '../../core';
import {
  COMBAT_DAMAGED,
  COMBAT_HEALED,
  COMBAT_KILLED,
  emitEvent,
} from '../rpg-core/events';
import { getDataRegistry } from '../rpg-core/registry';

export const Health = defineComponent({
  current: F32,
  max: F32,
  /** Invulnerability countdown (seconds) — `damageHealth` ignores blows while > 0. */
  invulnTimer: F32,
});

export const ProjectileData = defineComponent({
  damage: F32,
  ownerEid: I32,
  lifetime: F32,
  age: F32,
});

// `maxLife` is the authoritative lifetime for `spawnProjectile` entities;
// `ProjectileCleanupSystem` prefers it over the legacy `ProjectileData.lifetime`.
export const ProjectileConfig = defineComponent({
  speed: F32,
  maxLife: F32,
  damage: F32,
  faction: U8,
});

export const FactionComponent = defineComponent({
  tag: U8,
});

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

/** Grant temporary invulnerability (i-frames). New grants extend, never shorten. */
export function grantInvulnerability(eid: number, seconds: number): void {
  if (seconds <= 0) return;
  Health.invulnTimer[eid] = Math.max(Health.invulnTimer[eid], seconds);
}

/**
 * Damage-pipeline hook: receives `(targetEid, amount, sourceEid)` and returns
 * the adjusted damage. Returning `<= 0` negates the blow entirely (no HP
 * change, no events) — blocks, parries and armors are built on this. Order of
 * registration is the order of application; each modifier sees the previous
 * modifier's output.
 */
export type DamageModifier = (
  eid: number,
  amount: number,
  source: number
) => number;

const damageModifiers: DamageModifier[] = [];

/** Register a damage modifier; returns an unregister function. */
export function registerDamageModifier(fn: DamageModifier): () => void {
  damageModifiers.push(fn);
  return () => {
    const idx = damageModifiers.indexOf(fn);
    if (idx >= 0) damageModifiers.splice(idx, 1);
  };
}

/** Drop every registered modifier (tests / HMR teardown). */
export function clearDamageModifiers(): void {
  damageModifiers.length = 0;
}

export function damageHealth(
  eid: number,
  amount: number,
  source: number = 0
): void {
  const current = Health.current[eid];
  if (current <= 0) return;
  // i-frames: the blow is ignored outright (no HP change, no events) so
  // watcher systems never read phantom hits during the grace window.
  if (Health.invulnTimer[eid] > 0) return;
  for (const modify of damageModifiers) {
    amount = modify(eid, amount, source);
  }
  if (amount <= 0) return;
  const newHp = Math.max(0, current - amount);
  Health.current[eid] = newHp;
  if (!activeState) return;
  // `attacker` only rides along when known (source !== 0) so legacy payload
  // consumers doing strict payload equality keep seeing the old shape.
  const attackerFields = source > 0 ? { attacker: source } : {};
  emitEvent(activeState, COMBAT_DAMAGED, {
    target: eid,
    amount,
    newHp,
    ...attackerFields,
  });
  if (newHp <= 0) {
    emitEvent(activeState, COMBAT_KILLED, { target: eid, ...attackerFields });
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

/** `current / max` clamped to [0, 1]; treats an unset max as 1. */
export function healthFraction(eid: number): number {
  const max = Health.max[eid] || 1;
  const frac = Health.current[eid] / max;
  return frac < 0 ? 0 : frac > 1 ? 1 : frac;
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
