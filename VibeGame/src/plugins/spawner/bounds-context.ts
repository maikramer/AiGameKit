import type { State } from '../../core';

/**
 * GLB URL used for the deferred AABB lift of a `TerrainSpawned` entity.
 *
 * ECS arrays can't hold strings, so the URL is kept in this per-`State`
 * `Map` keyed by entity id. Set by `spawnTemplateAtTerrain` when
 * `ground-align="aabb"` skips the lift because the bounds weren't cached yet,
 * and consumed by `TerrainSpawnBoundsCatchUpSystem` once the bounds arrive.
 */
const stateToAabbUrls = new WeakMap<State, Map<number, string>>();

export function getAabbPendingUrls(state: State): Map<number, string> {
  let m = stateToAabbUrls.get(state);
  if (!m) {
    m = new Map();
    stateToAabbUrls.set(state, m);
  }
  return m;
}

export function setAabbPendingUrl(
  state: State,
  entity: number,
  url: string
): void {
  getAabbPendingUrls(state).set(entity, url);
}

export function clearAabbPendingUrl(state: State, entity: number): void {
  getAabbPendingUrls(state).delete(entity);
}
