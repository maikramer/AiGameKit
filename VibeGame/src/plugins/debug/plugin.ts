import { defineQuery, type Component } from 'bitecs';
import type { Plugin, State } from '../../core';

export interface VibeGameDebugBridge {
  state: State;
  snapshot(options?: Record<string, unknown>): string;
  entities(): Array<{
    eid: number;
    name: string | null;
    components: Record<string, Record<string, number>>;
  }>;
  entity(name: string): {
    eid: number;
    name: string;
    components: Record<string, Record<string, number>>;
  } | null;
  component(eid: number, name: string): Record<string, number> | null;
  query(...componentNames: string[]): number[];
  componentNames(): string[];
  namedEntities(): Array<{ name: string; eid: number }>;
  step(dt?: number): void;
}

type TypedArrayField =
  | Float32Array
  | Int32Array
  | Uint8Array
  | Uint16Array
  | Uint32Array;

function isTypedArrayField(v: unknown): v is TypedArrayField {
  return (
    v instanceof Float32Array ||
    v instanceof Int32Array ||
    v instanceof Uint8Array ||
    v instanceof Uint16Array ||
    v instanceof Uint32Array
  );
}

function extractComponentFields(
  state: State,
  eid: number,
  compName: string
): Record<string, number> | null {
  const comp = state.getComponent(compName);
  if (!comp || !state.hasComponent(eid, comp)) return null;
  const fields: Record<string, number> = {};
  for (const key in comp) {
    if (key.startsWith('_')) continue;
    const field = (comp as Record<string, unknown>)[key];
    if (isTypedArrayField(field)) {
      fields[key] = field[eid];
    }
  }
  return fields;
}

function extractAllComponents(
  state: State,
  eid: number
): Record<string, Record<string, number>> {
  const result: Record<string, Record<string, number>> = {};
  for (const compName of state.getComponentNames()) {
    const fields = extractComponentFields(state, eid, compName);
    if (fields) result[compName] = fields;
  }
  return result;
}

function createBridge(state: State): VibeGameDebugBridge {
  return {
    state,
    snapshot(options) {
      return state
        .snapshot(options as Parameters<State['snapshot']>[0])
        .format();
    },
    entities() {
      const snap = state.snapshot();
      return snap.entities.map((e) => ({
        eid: e.eid,
        name: e.name ?? null,
        components: e.components,
      }));
    },
    entity(name) {
      const eid = state.getEntityByName(name);
      if (eid === null) return null;
      const entityName = state.getEntityName(eid) ?? name;
      const components = extractAllComponents(state, eid);
      return { eid, name: entityName, components };
    },
    component(eid, name) {
      return extractComponentFields(state, eid, name);
    },
    query(...componentNames) {
      const components = componentNames
        .map((n) => state.getComponent(n))
        .filter((c): c is Component => c != null);
      if (components.length === 0) return [];
      const q = defineQuery(components);
      return Array.from(q(state.world));
    },
    componentNames() {
      return state.getComponentNames();
    },
    namedEntities() {
      const entries = Array.from(state.getNamedEntities().entries());
      return entries.map(([name, eid]) => ({ name, eid }));
    },
    step(dt) {
      state.step(dt);
    },
  };
}

export const DebugPlugin: Plugin = {
  initialize(state: State): void {
    if (typeof window === 'undefined') return;

    const w = window as unknown as Record<string, unknown>;
    if (w.__VIBEGAME__) return;

    w.__VIBEGAME__ = createBridge(state);
  },
};
