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
  // Cleanup must ride the destroy callback, not an exists() sweep: entity ids
  // are recycled, so a sweep can see the id alive again (now a different
  // entity) and keep syncing this mesh to it — e.g. a destroyed rock's GLB
  // reappearing glued to the pickup popup that reused its id.
  state.onDestroy(entity, () => {
    group.removeFromParent();
    const map = roots.get(state);
    if (map && map.get(entity) === group) map.delete(entity);
  });
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

/**
 * Backup sweep for entries whose destroy callback never ran (evita fugas no
 * `Map`). The primary cleanup is the `onDestroy` hook in
 * {@link registerGltfRootGroup} — this sweep alone is unsafe against entity-id
 * recycling.
 */
export function pruneStaleGltfRootGroups(state: State): void {
  const m = roots.get(state);
  if (!m) return;
  for (const [eid, group] of [...m.entries()]) {
    if (!state.exists(eid)) {
      group.removeFromParent();
      m.delete(eid);
    }
  }
}
