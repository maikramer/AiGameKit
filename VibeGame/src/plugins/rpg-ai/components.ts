import { MAX_ENTITIES } from '../../core/ecs/constants';
import type { State } from '../../core';

let _aiRng: () => number = Math.random;

export function setAiRng(rng: () => number): void {
  _aiRng = rng;
}

export function resetAiRng(): void {
  _aiRng = Math.random;
}

export function aiRandom(): number {
  return _aiRng();
}

/** Registry kind under which preset configs are stored. */
export const MELEE_AI_KIND = 'melee-ai';

export const AI_MODE_IDLE = 0;
export const AI_MODE_DETECT = 1;
export const AI_MODE_CHASE = 2;
export const AI_MODE_ATTACK = 3;
export const AI_MODE_LUNGE = 4;
export const AI_MODE_DEAD = 5;

export type AiMode =
  | typeof AI_MODE_IDLE
  | typeof AI_MODE_DETECT
  | typeof AI_MODE_CHASE
  | typeof AI_MODE_ATTACK
  | typeof AI_MODE_LUNGE
  | typeof AI_MODE_DEAD;

/**
 * Queryable, serializable AI state (the rich config lives in the per-entity
 * config side-table).
 */
export const AiStateComponent = {
  mode: new Uint8Array(MAX_ENTITIES),
  target: new Uint32Array(MAX_ENTITIES),
  cooldown: new Float32Array(MAX_ENTITIES),
  leash: new Float32Array(MAX_ENTITIES),
} as const;

/** Melee AI tuning. Defaults mirror the simple-rpg creature prototype. */
export interface MeleeAiConfig {
  /** Distance at which a hostile target is first noticed (idle→detect). */
  detectRange: number;
  /** Distance at which melee attacks begin (chase→attack). */
  attackRange: number;
  /** Seconds between attacks (lunge cooldown). */
  attackCooldown: number;
  /** Damage applied on a successful lunge hit. */
  attackDamage: number;
  /** Movement speed while chasing. */
  chaseSpeed: number;
  /** Movement speed while wandering idle. */
  wanderSpeed: number;
  /** Max distance (from spawn origin) the creature wanders while idle. */
  wanderRadius: number;
  /** Max distance (from spawn origin) the creature pursues before leashing. */
  leashRadius: number;
  /** Seconds the creature braces before a lunge burst. */
  lungeWindup: number;
  /** Seconds the lunge burst travels. */
  lungeDuration: number;
  /** Seconds the creature pauses after a lunge. */
  lungeRecovery: number;
  /** Minimum gap kept between the creature and target during a lunge. */
  lungeStandoff: number;
  /** Min seconds hovering in place while idle before picking a wander point. */
  hoverMin: number;
  /** Max seconds hovering in place while idle before picking a wander point. */
  hoverMax: number;
  /**
   * Optional fixed target entity. When set (>0) the hostile-query is skipped
   * and this entity is always targeted while alive. When omitted, the nearest
   * hostile (via `isHostile`) is acquired.
   */
  targetEid?: number;
  // ── Optional behaviour enrichment (all back-compatible / off by default) ──
  /** Orbit the target while waiting between swings instead of standing still. */
  strafe?: boolean;
  /** Below this HP fraction, back off + circle (kite) instead of pressing in. */
  lowHpKiteFrac?: number;
  /** Below this HP fraction, enrage: faster + shorter cooldown. */
  enrageBelowFrac?: number;
  /** Chase-speed multiplier while enraged (default 1.4). */
  enrageSpeedMult?: number;
  /** Attack-cooldown multiplier while enraged (default 0.5). */
  enrageCooldownMult?: number;
}

export interface AiInstanceState {
  originX: number;
  originZ: number;
  originSet: boolean;
  lungePhase: 'ready' | 'windup' | 'lunge' | 'recovery';
  lungeTimer: number;
  lungeDirX: number;
  lungeDirZ: number;
  detectTimer: number;
  idleTimer: number;
  hovering: boolean;
  wanderX: number;
  wanderZ: number;
  /** Current orbit direction (+1/-1), flipped on a timer for strafe. */
  strafeDir: number;
  strafeTimer: number;
}

export function createAiInstanceState(): AiInstanceState {
  return {
    originX: 0,
    originZ: 0,
    originSet: false,
    lungePhase: 'ready',
    lungeTimer: 0,
    lungeDirX: 0,
    lungeDirZ: 1,
    detectTimer: 0,
    idleTimer: 0,
    hovering: true,
    wanderX: 0,
    wanderZ: 0,
    strafeDir: aiRandom() < 0.5 ? -1 : 1,
    strafeTimer: 0,
  };
}

const configByState = new WeakMap<State, Map<number, MeleeAiConfig>>();
const instanceByState = new WeakMap<State, Map<number, AiInstanceState>>();

function mapFor<K, V>(wm: WeakMap<State, Map<K, V>>, state: State): Map<K, V> {
  let m = wm.get(state);
  if (!m) {
    m = new Map();
    wm.set(state, m);
  }
  return m;
}

export function getMeleeAiConfig(
  state: State,
  eid: number
): MeleeAiConfig | undefined {
  return configByState.get(state)?.get(eid);
}

export function setMeleeAiConfig(
  state: State,
  eid: number,
  config: MeleeAiConfig
): void {
  mapFor(configByState, state).set(eid, config);
}

export function removeMeleeAiConfig(state: State, eid: number): void {
  configByState.get(state)?.delete(eid);
}

export function getOrCreateAiInstanceState(
  state: State,
  eid: number
): AiInstanceState {
  const m = mapFor(instanceByState, state);
  let inst = m.get(eid);
  if (!inst) {
    inst = createAiInstanceState();
    m.set(eid, inst);
  }
  return inst;
}

export function removeAiInstanceState(state: State, eid: number): void {
  instanceByState.get(state)?.delete(eid);
}
