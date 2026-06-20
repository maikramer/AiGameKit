import { logger } from '../../core/utils/logger';
import type { State, System } from '../../core';

export type EventHandler = (payload: unknown) => void;

export interface SubscriptionOptions {
  /** When set, the cleanup system removes this subscription once the entity is destroyed. */
  readonly entityRef?: number;
}

interface Subscription {
  readonly id: number;
  readonly handler: EventHandler;
  readonly entityRef?: number;
}

/**
 * Synchronous publish/subscribe event bus. Handlers fire immediately during
 * {@link emit} in subscription order. For asynchronous flows use the
 * coroutine system (`startCoroutine`).
 */
export class EventBus {
  private nextId = 1;
  private readonly subsByEvent = new Map<string, Subscription[]>();
  private readonly idToEvent = new Map<number, string>();

  on(event: string, handler: EventHandler, opts?: SubscriptionOptions): number {
    return this.add(event, handler, opts);
  }

  once(
    event: string,
    handler: EventHandler,
    opts?: SubscriptionOptions
  ): number {
    const id = this.nextId;
    const wrapper: EventHandler = (payload) => {
      this.remove(id, event);
      handler(payload);
    };
    return this.add(event, wrapper, opts, id);
  }

  off(subscriptionId: number): void {
    const event = this.idToEvent.get(subscriptionId);
    if (event === undefined) return;
    this.remove(subscriptionId, event);
  }

  emit(event: string, payload: unknown): void {
    const subs = this.subsByEvent.get(event);
    if (!subs || subs.length === 0) return;
    const snapshot = subs.slice();
    for (const sub of snapshot) {
      try {
        sub.handler(payload);
      } catch (err) {
        logger.error(`[EventBus] handler error for "${event}":`, err);
      }
    }
  }

  clear(event?: string): void {
    if (event !== undefined) {
      const subs = this.subsByEvent.get(event);
      if (!subs) return;
      for (const sub of subs) this.idToEvent.delete(sub.id);
      this.subsByEvent.delete(event);
      return;
    }
    for (const subs of this.subsByEvent.values()) {
      for (const sub of subs) this.idToEvent.delete(sub.id);
    }
    this.subsByEvent.clear();
  }

  cleanupDestroyedEntities(exists: (eid: number) => boolean): void {
    for (const [event, subs] of this.subsByEvent) {
      for (let i = subs.length - 1; i >= 0; i--) {
        const ref = subs[i].entityRef;
        if (ref !== undefined && !exists(ref)) {
          this.idToEvent.delete(subs[i].id);
          subs.splice(i, 1);
        }
      }
      if (subs.length === 0) {
        this.subsByEvent.delete(event);
      }
    }
  }

  private add(
    event: string,
    handler: EventHandler,
    opts: SubscriptionOptions | undefined,
    reuseId?: number
  ): number {
    const id = reuseId ?? this.nextId++;
    const sub: Subscription = { id, handler, entityRef: opts?.entityRef };
    let subs = this.subsByEvent.get(event);
    if (!subs) {
      subs = [];
      this.subsByEvent.set(event, subs);
    }
    subs.push(sub);
    this.idToEvent.set(id, event);
    return id;
  }

  private remove(subId: number, event: string): void {
    const subs = this.subsByEvent.get(event);
    if (!subs) return;
    const idx = subs.findIndex((s) => s.id === subId);
    if (idx === -1) return;
    subs.splice(idx, 1);
    this.idToEvent.delete(subId);
    if (subs.length === 0) {
      this.subsByEvent.delete(event);
    }
  }
}

const buses = new WeakMap<State, EventBus>();

export function getEventBus(state: State): EventBus {
  let bus = buses.get(state);
  if (!bus) {
    bus = new EventBus();
    buses.set(state, bus);
  }
  return bus;
}

export function emitEvent(state: State, event: string, payload: unknown): void {
  getEventBus(state).emit(event, payload);
}

export function onEvent(
  state: State,
  event: string,
  handler: EventHandler,
  opts?: SubscriptionOptions
): number {
  return getEventBus(state).on(event, handler, opts);
}

export const EventBusCleanupSystem: System = {
  group: 'simulation',
  update(state: State): void {
    getEventBus(state).cleanupDestroyedEntities((eid) => state.exists(eid));
  },
};

export const COMBAT_DAMAGED = 'combat:damaged';
export const COMBAT_HEALED = 'combat:healed';
export const COMBAT_KILLED = 'combat:killed';
export const COMBAT_DEATH = 'combat:death';
export const ECONOMY_SPENT = 'economy:spent';
export const ECONOMY_GAINED = 'economy:gained';
export const INVENTORY_ADDED = 'inventory:added';
export const INVENTORY_REMOVED = 'inventory:removed';
export const PROGRESSION_LEVEL_UP = 'progression:level-up';
export const PROGRESSION_XP_GAINED = 'progression:xp-gained';
export const PROGRESSION_SKILL_PURCHASED = 'progression:skill-purchased';
export const STATUS_APPLIED = 'status:applied';
export const STATUS_EXPIRED = 'status:expired';
export const STATUS_CANCELLED = 'status:cancelled';
export const LOOT_ROLLED = 'loot:rolled';
export const LOOT_DROPPED = 'loot:dropped';
