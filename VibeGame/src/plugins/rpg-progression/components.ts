import { MAX_ENTITIES } from '../../core/ecs/constants';
import type { State } from '../../core';
import {
  PROGRESSION_LEVEL_UP,
  PROGRESSION_SKILL_PURCHASED,
  PROGRESSION_XP_GAINED,
  emitEvent,
  getDataRegistry,
  onEvent,
} from '../rpg-core';
import type { SkillDef, StatModifier } from '../rpg-core/types';

export const ProgressionComponent = {
  xp: new Float64Array(MAX_ENTITIES),
  level: new Uint16Array(MAX_ENTITIES),
  unspentPoints: new Uint16Array(MAX_ENTITIES),
  spent: new Uint16Array(MAX_ENTITIES),
} as const;

export const DEFAULT_SKILL_POINTS_PER_LEVEL = 3;
const DEFAULT_XP_CURVE_ID = 'default';

interface ProgressionEntityConfig {
  xpCurve: string;
  skillPointsPerLevel: number;
}

interface PendingEvent {
  readonly event: string;
  readonly payload: unknown;
}

interface ProgressionTables {
  readonly config: Map<number, ProgressionEntityConfig>;
  readonly ranks: Map<number, Map<string, number>>;
  pendingEvents: PendingEvent[];
}

const tablesByState = new WeakMap<State, ProgressionTables>();

function getTables(state: State): ProgressionTables {
  let tables = tablesByState.get(state);
  if (!tables) {
    tables = {
      config: new Map(),
      ranks: new Map(),
      pendingEvents: [],
    };
    tablesByState.set(state, tables);
  }
  return tables;
}

export function getProgressionConfig(
  state: State,
  eid: number
): ProgressionEntityConfig {
  const tables = getTables(state);
  let cfg = tables.config.get(eid);
  if (!cfg) {
    cfg = {
      xpCurve: DEFAULT_XP_CURVE_ID,
      skillPointsPerLevel: DEFAULT_SKILL_POINTS_PER_LEVEL,
    };
    tables.config.set(eid, cfg);
  }
  return cfg;
}

export function setProgressionConfig(
  state: State,
  eid: number,
  cfg: Partial<ProgressionEntityConfig>
): void {
  const current = getProgressionConfig(state, eid);
  if (cfg.xpCurve !== undefined) current.xpCurve = cfg.xpCurve;
  if (cfg.skillPointsPerLevel !== undefined) {
    current.skillPointsPerLevel = cfg.skillPointsPerLevel;
  }
}

function getSkillRanks(state: State, eid: number): Map<string, number> {
  const tables = getTables(state);
  let ranks = tables.ranks.get(eid);
  if (!ranks) {
    ranks = new Map();
    tables.ranks.set(eid, ranks);
  }
  return ranks;
}

type XpCurveFn = (level: number) => number;

interface XpCurveDef {
  id: string;
  fn?: XpCurveFn;
}

function resolveXpCurve(state: State, curveId: string): XpCurveFn {
  const registry = getDataRegistry(state);
  const def = registry.get<XpCurveDef>('xp-curve', curveId);
  if (def?.fn) return def.fn;
  if (curveId !== DEFAULT_XP_CURVE_ID) {
    const fallback = registry.get<XpCurveDef>('xp-curve', DEFAULT_XP_CURVE_ID);
    if (fallback?.fn) return fallback.fn;
  }
  return (lvl) => 5 + lvl;
}

function queueEvent(state: State, event: string, payload: unknown): void {
  getTables(state).pendingEvents.push({ event, payload });
}

export function addXp(state: State, eid: number, amount: number): void {
  ProgressionComponent.xp[eid] += amount;
  const cfg = getProgressionConfig(state, eid);
  const curve = resolveXpCurve(state, cfg.xpCurve);
  const startingLevel = ProgressionComponent.level[eid];
  while (
    ProgressionComponent.xp[eid] >= curve(ProgressionComponent.level[eid])
  ) {
    levelUp(state, eid);
  }
  const gained = ProgressionComponent.level[eid] > startingLevel;
  queueEvent(state, PROGRESSION_XP_GAINED, {
    eid,
    amount,
    total: ProgressionComponent.xp[eid],
    leveledUp: gained,
  });
}

export function levelUp(state: State, eid: number): void {
  const cfg = getProgressionConfig(state, eid);
  const curve = resolveXpCurve(state, cfg.xpCurve);
  const currentLevel = ProgressionComponent.level[eid];
  const needed = curve(currentLevel);
  ProgressionComponent.xp[eid] -= needed;
  ProgressionComponent.level[eid] = currentLevel + 1;
  ProgressionComponent.unspentPoints[eid] += cfg.skillPointsPerLevel;
  queueEvent(state, PROGRESSION_LEVEL_UP, { eid, level: currentLevel + 1 });
}

/**
 * XP required to advance from the entity's current level to the next one,
 * resolving the active xp-curve (registry → default `5 + level` fallback).
 * Returns 0 when the entity has no ProgressionComponent.
 */
export function getXpToNextLevel(state: State, eid: number): number {
  if (!state.hasComponent(eid, ProgressionComponent)) return 0;
  const cfg = getProgressionConfig(state, eid);
  const curve = resolveXpCurve(state, cfg.xpCurve);
  return curve(ProgressionComponent.level[eid]);
}

function computeSkillCost(def: SkillDef, currentRank: number): number {
  if (Array.isArray(def.cost)) {
    const idx = Math.min(currentRank, def.cost.length - 1);
    return def.cost[idx] ?? 1;
  }
  return def.cost;
}

interface EventTriggerPayload {
  event: string;
  triggers: string;
}

function isStatModifierPayload(p: unknown): p is StatModifier {
  if (typeof p !== 'object' || p === null) return false;
  const m = p as Record<string, unknown>;
  return (
    typeof m.stat === 'string' &&
    typeof m.magnitude === 'number' &&
    (m.stackMode === 'replace' ||
      m.stackMode === 'stack' ||
      m.stackMode === 'max')
  );
}

function isEventTriggerPayload(p: unknown): p is EventTriggerPayload {
  if (typeof p !== 'object' || p === null) return false;
  const m = p as Record<string, unknown>;
  return typeof m.event === 'string' && typeof m.triggers === 'string';
}

function applySkillEffect(
  state: State,
  eid: number,
  def: SkillDef,
  newRank: number
): void {
  if (def.effect.kind === 'event-trigger') {
    const payload = def.effect.payload;
    if (!isEventTriggerPayload(payload)) return;
    onEvent(
      state,
      payload.event,
      () => {
        emitEvent(state, payload.triggers, {
          eid,
          skillId: def.id,
          rank: newRank,
        });
      },
      { entityRef: eid }
    );
  }
}

export function spendSkillPoint(
  state: State,
  eid: number,
  skillId: string
): boolean {
  if (ProgressionComponent.unspentPoints[eid] <= 0) return false;
  const def = getDataRegistry(state).get<SkillDef>('skill', skillId);
  if (!def) return false;
  const ranks = getSkillRanks(state, eid);
  const currentRank = ranks.get(skillId) ?? 0;
  if (currentRank >= def.maxRank) return false;
  const cost = computeSkillCost(def, currentRank);
  if (ProgressionComponent.unspentPoints[eid] < cost) return false;

  ProgressionComponent.unspentPoints[eid] -= cost;
  ProgressionComponent.spent[eid] += cost;
  const newRank = currentRank + 1;
  ranks.set(skillId, newRank);
  applySkillEffect(state, eid, def, newRank);
  queueEvent(state, PROGRESSION_SKILL_PURCHASED, {
    eid,
    skillId,
    rank: newRank,
  });
  return true;
}

export function getSkillRank(
  state: State,
  eid: number,
  skillId: string
): number {
  return getSkillRanks(state, eid).get(skillId) ?? 0;
}

export function getStatModifiers(state: State, eid: number): StatModifier[] {
  const ranks = getSkillRanks(state, eid);
  if (ranks.size === 0) return [];
  const registry = getDataRegistry(state);
  const mods: StatModifier[] = [];
  for (const [skillId, rank] of ranks) {
    const def = registry.get<SkillDef>('skill', skillId);
    if (!def || def.effect.kind !== 'stat-modifier') continue;
    const payload = def.effect.payload;
    if (!isStatModifierPayload(payload)) continue;
    const magnitude =
      payload.stackMode === 'stack'
        ? payload.magnitude * rank
        : payload.magnitude;
    mods.push({
      stat: payload.stat,
      magnitude,
      duration: payload.duration,
      stackMode: payload.stackMode,
    });
  }
  return mods;
}

export function drainPendingEvents(state: State): PendingEvent[] {
  const tables = getTables(state);
  const drained = tables.pendingEvents;
  tables.pendingEvents = [];
  return drained;
}

export interface ProgressionEntitySnapshot {
  xp: number;
  level: number;
  unspentPoints: number;
  spent: number;
  xpCurve: string;
  skillPointsPerLevel: number;
  ranks: Record<string, number>;
}

export function getProgressionEntitySnapshot(
  state: State,
  eid: number
): ProgressionEntitySnapshot | null {
  if (!state.hasComponent(eid, ProgressionComponent)) return null;
  const cfg = getProgressionConfig(state, eid);
  const ranks = getSkillRanks(state, eid);
  const rankObj: Record<string, number> = {};
  for (const [skillId, rank] of ranks) rankObj[skillId] = rank;
  return {
    xp: ProgressionComponent.xp[eid],
    level: ProgressionComponent.level[eid],
    unspentPoints: ProgressionComponent.unspentPoints[eid],
    spent: ProgressionComponent.spent[eid],
    xpCurve: cfg.xpCurve,
    skillPointsPerLevel: cfg.skillPointsPerLevel,
    ranks: rankObj,
  };
}

// Restores xp/level/points and skill ranks directly. Unlike addXp /
// spendSkillPoint this performs no curve recalculation, cost validation, or
// event emission: the snapshot already represents a consistent prior state.
export function applyProgressionEntitySnapshot(
  state: State,
  eid: number,
  data: ProgressionEntitySnapshot
): void {
  ProgressionComponent.xp[eid] = data.xp;
  ProgressionComponent.level[eid] = data.level;
  ProgressionComponent.unspentPoints[eid] = data.unspentPoints;
  ProgressionComponent.spent[eid] = data.spent;
  setProgressionConfig(state, eid, {
    xpCurve: data.xpCurve,
    skillPointsPerLevel: data.skillPointsPerLevel,
  });
  const ranks = getSkillRanks(state, eid);
  ranks.clear();
  for (const [skillId, rank] of Object.entries(data.ranks)) {
    ranks.set(skillId, rank);
  }
}
