import type { Group } from 'three';
import type { State } from '../../core';

const roots = new WeakMap<State, Map<number, Group>>();

export function registerGltfRootGroup(
  state: State,
  entity: number,
  group: Group
): void {
  let m = roots.get(state);
  if (!m) {
    m = new Map();
    roots.set(state, m);
  }
  m.set(entity, group);
}

export function getGltfRootGroup(
  state: State,
  entity: number
): Group | undefined {
  return roots.get(state)?.get(entity);
}

export function deleteGltfRootGroup(state: State, entity: number): void {
  roots.get(state)?.delete(entity);
}

/** Itera raízes GLB registadas (ex.: para sincronizar mesh com `WorldTransform`). */
export function forEachGltfRootGroup(
  state: State,
  fn: (entity: number, group: Group) => void
): void {
  const m = roots.get(state);
  if (!m) return;
  for (const [entity, group] of m) {
    fn(entity, group);
  }
}

/** Remove entradas de entidades que já não existem (evita fugas no `Map`). */
export function pruneStaleGltfRootGroups(state: State): void {
  const m = roots.get(state);
  if (!m) return;
  for (const eid of [...m.keys()]) {
    if (!state.exists(eid)) {
      m.delete(eid);
    }
  }
}
