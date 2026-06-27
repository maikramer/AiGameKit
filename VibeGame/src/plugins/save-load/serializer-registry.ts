import { getAllEntities } from 'bitecs';
import { defineQuery, type Component, type State } from '../../core';
import { Serializable } from './components';

// Opaque JSON-serializable blob. `unknown` (not Record<string, unknown>) so a
// serializer may return its own concrete interface without an index signature.
export type SerializedKind = unknown;

export interface SaveSerializer {
  serialize(state: State, eid: number): SerializedKind | null;
  deserialize(state: State, eid: number, data: SerializedKind): void;
}

export interface SerializableEntitySnapshot {
  eid: number;
  name?: string;
  kinds: Record<string, SerializedKind>;
}

export interface SaveSnapshot {
  version: '1.0';
  entities: SerializableEntitySnapshot[];
  /** Optional global (non-entity) state, keyed by serializer kind. */
  globals?: Record<string, SerializedKind>;
}

export interface GlobalSaveSerializer {
  serialize(state: State): SerializedKind | null;
  deserialize(state: State, data: SerializedKind): void;
}

const registries = new WeakMap<State, Map<string, SaveSerializer>>();
const globalRegistries = new WeakMap<
  State,
  Map<string, GlobalSaveSerializer>
>();

function getRegistry(state: State): Map<string, SaveSerializer> {
  let map = registries.get(state);
  if (!map) {
    map = new Map();
    registries.set(state, map);
  }
  return map;
}

function getGlobalRegistry(state: State): Map<string, GlobalSaveSerializer> {
  let map = globalRegistries.get(state);
  if (!map) {
    map = new Map();
    globalRegistries.set(state, map);
  }
  return map;
}

export function registerSaveSerializer(
  state: State,
  kind: string,
  serializer: SaveSerializer
): void {
  getRegistry(state).set(kind, serializer);
}

export function getSaveSerializer(
  state: State,
  kind: string
): SaveSerializer | undefined {
  return getRegistry(state).get(kind);
}

export function registerGlobalSaveSerializer(
  state: State,
  kind: string,
  serializer: GlobalSaveSerializer
): void {
  getGlobalRegistry(state).set(kind, serializer);
}

export interface TransientExclusion {
  readonly name: string;
  readonly component: string;
  readonly matches?: (
    state: State,
    eid: number,
    component: Component
  ) => boolean;
}

const transientExclusions: TransientExclusion[] = [];

export function registerTransientExclusion(
  exclusion: TransientExclusion
): void {
  if (!transientExclusions.some((e) => e.name === exclusion.name)) {
    transientExclusions.push(exclusion);
  }
}

export function isTransientEntity(state: State, eid: number): boolean {
  for (const exclusion of transientExclusions) {
    const component = state.getComponent(exclusion.component);
    if (component && state.hasComponent(eid, component)) {
      if (!exclusion.matches || exclusion.matches(state, eid, component)) {
        return true;
      }
    }
  }
  return false;
}

const serializableQuery = defineQuery([Serializable]);

export function serializeAll(state: State): SaveSnapshot {
  const serializers = getRegistry(state);
  const candidates = new Set<number>(getAllEntities(state.world));
  for (const eid of serializableQuery(state.world)) {
    if (Serializable.flag[eid]) candidates.add(eid);
  }

  const nameByEid = new Map<number, string>();
  for (const [name, eid] of state.getNamedEntities()) {
    nameByEid.set(eid, name);
  }

  const entities: SerializableEntitySnapshot[] = [];
  for (const eid of candidates) {
    if (!state.exists(eid)) continue;
    if (isTransientEntity(state, eid)) continue;

    const kinds: Record<string, SerializedKind> = {};
    for (const [kind, serializer] of serializers) {
      const data = serializer.serialize(state, eid);
      if (data) kinds[kind] = data;
    }
    if (Object.keys(kinds).length === 0 && !Serializable.flag[eid]) continue;

    entities.push({ eid, name: nameByEid.get(eid), kinds });
  }

  entities.sort((a, b) => a.eid - b.eid);

  const globalSerializers = getGlobalRegistry(state);
  const globals: Record<string, SerializedKind> = {};
  for (const [kind, gss] of globalSerializers) {
    const data = gss.serialize(state);
    if (data !== null && data !== undefined) globals[kind] = data;
  }

  return {
    version: '1.0',
    entities,
    ...(Object.keys(globals).length > 0 ? { globals } : {}),
  };
}

export function deserializeAll(state: State, snapshot: SaveSnapshot): void {
  const serializers = getRegistry(state);
  for (const entity of snapshot.entities) {
    const eid = state.exists(entity.eid) ? entity.eid : state.createEntity();
    for (const [kind, data] of Object.entries(entity.kinds)) {
      const serializer = serializers.get(kind);
      if (serializer) serializer.deserialize(state, eid, data);
    }
  }
  // Globals are optional (back-compat: snapshots without `globals` skip this).
  if (snapshot.globals) {
    const globalSerializers = getGlobalRegistry(state);
    for (const [kind, data] of Object.entries(snapshot.globals)) {
      const gss = globalSerializers.get(kind);
      if (gss) gss.deserialize(state, data);
    }
  }
}
