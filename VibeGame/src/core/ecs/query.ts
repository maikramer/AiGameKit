import { registerQuery, type World } from 'bitecs';

interface QueryCacheEntry {
  query: { dense: Uint32Array | number[] };
  out: number[];
}

/**
 * Cached query wrapper over bitecs 0.4 `registerQuery`.
 *
 * Returns a function `(world) => number[]` for use in system loops:
 *
 * ```ts
 * const bodyQuery = defineQuery([Rigidbody, Transform]);
 * for (const eid of bodyQuery(state.world)) { ... }
 * ```
 *
 * The result is a snapshot copied out of the live dense set, so it is safe to
 * add/remove entities or components while iterating it. To avoid allocating a
 * fresh array on every call (this runs for most systems every frame), the
 * snapshot is written into a per-world scratch array that is reused across
 * calls. Callers must consume the result before invoking the *same* query
 * function again — i.e. do not hold two simultaneous results of one query, and
 * do not nest iteration of the same query. Different query functions each have
 * their own scratch buffer and never interfere.
 */
export function defineQuery(
  components: Record<string, unknown>[]
): (world: World) => number[] {
  const cache = new WeakMap<World, QueryCacheEntry>();
  return (world: World): number[] => {
    let entry = cache.get(world);
    if (!entry) {
      entry = { query: registerQuery(world, components), out: [] };
      cache.set(world, entry);
    }
    // Read `.dense` fresh each call: bitecs may swap the backing array when the
    // sparse set grows.
    const dense = entry.query.dense;
    const out = entry.out;
    const n = dense.length;
    out.length = n;
    for (let i = 0; i < n; i++) out[i] = dense[i];
    return out;
  };
}
