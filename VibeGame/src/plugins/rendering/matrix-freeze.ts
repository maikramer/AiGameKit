import * as THREE from 'three';

/**
 * Freeze / thaw matrix updates for a hidden subtree.
 *
 * `visible = false` keeps the renderer out of a subtree, but
 * `Object3D.updateMatrixWorld` ignores visibility: every node under it is still
 * recomposed once per frame, for as long as it hangs off the scene. In a
 * dressed world most of the graph is hidden — culled props plus the *inactive*
 * LOD children of every rigged prop, each carrying its own skeleton — so those
 * invisible nodes can easily outnumber the drawn ones by 4:1 (simple-rpg:
 * ~12.3k of 15.4k scene nodes, over 11k of them bones).
 *
 * Clearing `matrixAutoUpdate` removes the per-node `compose()`; with no local
 * change to propagate, `matrixWorldNeedsUpdate` stays false and the world
 * matrix multiply is skipped too.
 *
 * Clearing `matrixWorldAutoUpdate` on the *root* removes the rest: the parent's
 * child loop skips a subtree whose root has it cleared, so the nodes below are
 * never visited at all. That is the part that actually costs — the flag walk
 * alone still paid one call per node per frame across ~12k hidden nodes, and
 * `updateMatrixWorld` was the single hottest JS frame in a simple-rpg profile
 * (~4% of the main thread).
 *
 * Thawing restores each node's original flag and forces one world-matrix
 * refresh, so a subtree that was posed/moved while frozen pops back correct.
 */
const savedMatrixAutoUpdate = new WeakMap<THREE.Object3D, boolean>();
const savedMatrixWorldAutoUpdate = new WeakMap<THREE.Object3D, boolean>();

export function setSubtreeMatrixFrozen(
  root: THREE.Object3D,
  frozen: boolean
): void {
  root.traverse((obj) => {
    if (frozen) {
      if (!savedMatrixAutoUpdate.has(obj)) {
        savedMatrixAutoUpdate.set(obj, obj.matrixAutoUpdate);
      }
      obj.matrixAutoUpdate = false;
      return;
    }
    const previous = savedMatrixAutoUpdate.get(obj);
    if (previous === undefined) return;
    obj.matrixAutoUpdate = previous;
    savedMatrixAutoUpdate.delete(obj);
    obj.matrixWorldNeedsUpdate = true;
  });

  if (frozen) {
    if (!savedMatrixWorldAutoUpdate.has(root)) {
      savedMatrixWorldAutoUpdate.set(root, root.matrixWorldAutoUpdate);
    }
    // Parent traversal now stops here: `Object3D.updateMatrixWorld` only
    // recurses into children whose `matrixWorldAutoUpdate` is set (or on a
    // forced update, which still reaches the subtree when something needs it).
    root.matrixWorldAutoUpdate = false;
    return;
  }

  const previousWorld = savedMatrixWorldAutoUpdate.get(root);
  if (previousWorld === undefined) return;
  root.matrixWorldAutoUpdate = previousWorld;
  savedMatrixWorldAutoUpdate.delete(root);
  // The subtree was skipped entirely while frozen, so one forced pass brings
  // every descendant's world matrix back in sync with the pose it thawed into.
  root.updateMatrixWorld(true);
}

/** True when the subtree root is currently frozen by {@link setSubtreeMatrixFrozen}. */
export function isSubtreeMatrixFrozen(root: THREE.Object3D): boolean {
  return savedMatrixAutoUpdate.has(root);
}
