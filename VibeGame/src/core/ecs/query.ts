import { registerQuery, type World } from 'bitecs';

/**
 * Cached query wrapper over bitecs 0.4 `registerQuery`.
 *
 * Returns a function `(world) => number[]` for use in system loops:
 *
 * ```ts
 * const bodyQuery = defineQuery([Rigidbody, Transform]);
 * for (const eid of bodyQuery(state.world)) { ... }
 * ```
 */
export function defineQuery(
  components: Record<string, unknown>[]
): (world: World) => number[] {
  const cache = new WeakMap<World, { dense: Uint32Array | number[] }>();
  return (world: World): number[] => {
    let q = cache.get(world);
    if (!q) {
      q = registerQuery(world, components);
      cache.set(world, q);
    }
    return Array.from(q.dense);
  };
}
