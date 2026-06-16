import type { Group } from 'three';
import type { State } from '../../core';

const groups = new WeakMap<State, Map<number, Group>>();

export function registerCompositionGroup(
  state: State,
  entity: number,
  group: Group
): void {
  let m = groups.get(state);
  if (!m) {
    m = new Map();
    groups.set(state, m);
  }
  m.set(entity, group);
  // Cleanup must ride the destroy callback, not an exists() sweep: entity ids
  // are recycled, so a sweep can see the id alive again (now a different
  // entity) and keep syncing this mesh to it.
  state.onDestroy(entity, () => {
    group.removeFromParent();
    disposeGroupMaterials(group);
    const map = groups.get(state);
    if (map && map.get(entity) === group) map.delete(entity);
  });
}

export function getCompositionGroup(
  state: State,
  entity: number
): Group | undefined {
  return groups.get(state)?.get(entity);
}

export function deleteCompositionGroup(state: State, entity: number): void {
  groups.get(state)?.delete(entity);
}

export function forEachCompositionGroup(
  state: State,
  fn: (entity: number, group: Group) => void
): void {
  const m = groups.get(state);
  if (!m) return;
  for (const [entity, group] of m) {
    fn(entity, group);
  }
}

// Backup sweep for entries whose destroy callback never ran. The primary
// cleanup is the onDestroy hook in registerCompositionGroup — this sweep alone
// is unsafe against entity-id recycling.
export function pruneStaleCompositionGroups(state: State): void {
  const m = groups.get(state);
  if (!m) return;
  for (const [eid, group] of [...m.entries()]) {
    if (!state.exists(eid)) {
      group.removeFromParent();
      disposeGroupMaterials(group);
      m.delete(eid);
    }
  }
}

function disposeGroupMaterials(group: Group): void {
  group.traverse((obj) => {
    const mesh = obj as unknown as {
      geometry?: import('three').BufferGeometry;
      material?: import('three').Material | import('three').Material[];
    };
    mesh.geometry?.dispose();
    const mat = mesh.material;
    if (Array.isArray(mat)) {
      mat.forEach((m) => m.dispose());
    } else if (mat) {
      mat.dispose();
    }
  });
}
