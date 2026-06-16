import { MAX_ENTITIES } from '../../core/ecs/constants';
import type { State } from '../../core';
import {
  COMBAT_DEATH,
  STATUS_APPLIED,
  STATUS_CANCELLED,
  STATUS_EXPIRED,
  getDataRegistry,
  onEvent,
} from '../rpg-core';
import type {
  SkillEffect,
  StatModifier,
  StatusEffectDef,
} from '../rpg-core/types';

export const StatusEffectComponent = {
  count: new Uint8Array(MAX_ENTITIES),
  version: new Uint32Array(MAX_ENTITIES),
} as const;

export interface ActiveStatusEffect {
  readonly defId: string;
  remainingTime: number;
  tickElapsed: number;
  modifiers: StatModifier[];
}

export type StackMode = 'replace' | 'stack' | 'max';

export interface StatusApplyOptions {
  readonly stackMode?: StackMode;
}

export const STATUS_KIND = 'status';

const EMPTY_LIST: readonly ActiveStatusEffect[] = Object.freeze([]);

interface PendingEvent {
  readonly event: string;
  readonly payload: unknown;
}

interface StatusTables {
  readonly active: Map<number, ActiveStatusEffect[]>;
  pendingEvents: PendingEvent[];
  deathSubscribed: boolean;
}

const tablesByState = new WeakMap<State, StatusTables>();

function getTables(state: State): StatusTables {
  let tables = tablesByState.get(state);
  if (!tables) {
    tables = { active: new Map(), pendingEvents: [], deathSubscribed: false };
    tablesByState.set(state, tables);
  }
  return tables;
}

function resolveDef(state: State, defId: string): StatusEffectDef | undefined {
  return getDataRegistry(state).get<StatusEffectDef>(STATUS_KIND, defId);
}

function bumpVersion(eid: number): void {
  const next = StatusEffectComponent.version[eid] + 1;
  StatusEffectComponent.version[eid] = next > 0xffffffff ? 1 : next;
}

function recalcCount(state: State, eid: number): void {
  const list = getTables(state).active.get(eid);
  StatusEffectComponent.count[eid] = list ? list.length : 0;
  bumpVersion(eid);
}

function queueEvent(state: State, event: string, payload: unknown): void {
  getTables(state).pendingEvents.push({ event, payload });
}

export function drainPendingEvents(state: State): PendingEvent[] {
  const tables = getTables(state);
  const drained = tables.pendingEvents;
  tables.pendingEvents = [];
  return drained;
}

function cloneModifiers(mods: readonly StatModifier[]): StatModifier[] {
  return mods.map((m) => ({ ...m }));
}

function ensureComponent(state: State, eid: number): void {
  if (state.hasComponent(eid, StatusEffectComponent)) return;
  state.addComponent(eid, StatusEffectComponent);
  state.onDestroy(eid, () => {
    getTables(state).active.delete(eid);
  });
}

export function ensureDeathSubscription(state: State): void {
  const tables = getTables(state);
  if (tables.deathSubscribed) return;
  tables.deathSubscribed = true;
  onEvent(state, COMBAT_DEATH, (payload) => {
    const target = (payload as { target?: number }).target;
    if (typeof target !== 'number') return;
    cancelAllStatuses(state, target);
  });
}

export function applyStatus(
  state: State,
  eid: number,
  defId: string,
  options?: StatusApplyOptions
): void {
  const def = resolveDef(state, defId);
  if (!def) return;
  ensureDeathSubscription(state);
  ensureComponent(state, eid);

  const tables = getTables(state);
  let list = tables.active.get(eid);
  if (!list) {
    list = [];
    tables.active.set(eid, list);
  }

  const stackMode = options?.stackMode ?? 'replace';
  const idx = list.findIndex((s) => s.defId === defId);
  if (idx >= 0) {
    const existing = list[idx];
    if (stackMode === 'replace') {
      existing.remainingTime = def.duration;
      existing.tickElapsed = 0;
      existing.modifiers = cloneModifiers(def.modifiers);
    } else if (stackMode === 'stack') {
      existing.remainingTime += def.duration;
    } else {
      existing.remainingTime = Math.max(existing.remainingTime, def.duration);
    }
  } else {
    list.push({
      defId,
      remainingTime: def.duration,
      tickElapsed: 0,
      modifiers: cloneModifiers(def.modifiers),
    });
  }

  recalcCount(state, eid);
  queueEvent(state, STATUS_APPLIED, { eid, defId, stackMode });
}

export function cancelStatus(state: State, eid: number, defId: string): void {
  const tables = getTables(state);
  const list = tables.active.get(eid);
  if (!list) return;
  const idx = list.findIndex((s) => s.defId === defId);
  if (idx < 0) return;
  list.splice(idx, 1);
  recalcCount(state, eid);
  queueEvent(state, STATUS_CANCELLED, { eid, defId });
}

export function cancelAllStatuses(state: State, eid: number): void {
  const tables = getTables(state);
  const list = tables.active.get(eid);
  if (!list || list.length === 0) return;
  const cancelled = list.map((s) => s.defId);
  list.length = 0;
  recalcCount(state, eid);
  for (const defId of cancelled) {
    queueEvent(state, STATUS_CANCELLED, { eid, defId });
  }
}

export function getActiveStatuses(
  state: State,
  eid: number
): readonly ActiveStatusEffect[] {
  const list = getTables(state).active.get(eid);
  if (!list || list.length === 0) return EMPTY_LIST;
  return list.slice();
}

export function getStatusModifiers(state: State, eid: number): StatModifier[] {
  const list = getTables(state).active.get(eid);
  if (!list || list.length === 0) return [];
  const mods: StatModifier[] = [];
  for (const status of list) {
    for (const m of status.modifiers) mods.push({ ...m });
  }
  return mods;
}

interface TickEventPayload {
  readonly triggers: string;
  readonly [key: string]: unknown;
}

function isTickEventPayload(p: unknown): p is TickEventPayload {
  if (typeof p !== 'object' || p === null) return false;
  return typeof (p as { triggers?: unknown }).triggers === 'string';
}

function applyTickEffect(
  state: State,
  eid: number,
  def: StatusEffectDef
): void {
  const effect: SkillEffect | undefined = def.tickEffect;
  if (!effect || effect.kind !== 'event-trigger') return;
  if (!isTickEventPayload(effect.payload)) return;
  const { triggers, ...rest } = effect.payload;
  queueEvent(state, triggers, { ...rest, eid, defId: def.id });
}

export function tickStatusEffects(state: State, dt: number): void {
  if (dt <= 0) return;
  const tables = getTables(state);
  if (tables.active.size === 0) return;
  const registry = getDataRegistry(state);

  for (const [eid, list] of tables.active) {
    if (list.length === 0) continue;
    for (let i = list.length - 1; i >= 0; i--) {
      const status = list[i];
      status.remainingTime -= dt;

      const def = registry.get<StatusEffectDef>(STATUS_KIND, status.defId);
      if (def?.tickInterval && def.tickInterval > 0) {
        status.tickElapsed += dt;
        while (status.tickElapsed >= def.tickInterval) {
          status.tickElapsed -= def.tickInterval;
          applyTickEffect(state, eid, def);
        }
      }

      if (status.remainingTime <= 0) {
        list.splice(i, 1);
        queueEvent(state, STATUS_EXPIRED, { eid, defId: status.defId });
      }
    }
    recalcCount(state, eid);
  }
}

export interface ActiveStatusEffectData {
  defId: string;
  remainingTime: number;
  tickElapsed: number;
}

export interface StatusEffectEntitySnapshot {
  effects: ActiveStatusEffectData[];
}

export function getStatusEffectEntitySnapshot(
  state: State,
  eid: number
): StatusEffectEntitySnapshot | null {
  if (!state.hasComponent(eid, StatusEffectComponent)) return null;
  const list = getTables(state).active.get(eid);
  if (!list || list.length === 0) return { effects: [] };
  return {
    effects: list.map((s) => ({
      defId: s.defId,
      remainingTime: s.remainingTime,
      tickElapsed: s.tickElapsed,
    })),
  };
}

// Restores active effects verbatim. Modifiers are re-resolved from the def when
// available (they are derived data, not authored save state); missing defs get
// empty modifiers. No STATUS_APPLIED events fire on restore.
export function applyStatusEffectEntitySnapshot(
  state: State,
  eid: number,
  data: StatusEffectEntitySnapshot
): void {
  ensureDeathSubscription(state);
  ensureComponent(state, eid);
  const tables = getTables(state);
  let list = tables.active.get(eid);
  if (!list) {
    list = [];
    tables.active.set(eid, list);
  }
  list.length = 0;
  for (const e of data.effects) {
    const def = resolveDef(state, e.defId);
    list.push({
      defId: e.defId,
      remainingTime: e.remainingTime,
      tickElapsed: e.tickElapsed ?? 0,
      modifiers: def ? cloneModifiers(def.modifiers) : [],
    });
  }
  recalcCount(state, eid);
}
