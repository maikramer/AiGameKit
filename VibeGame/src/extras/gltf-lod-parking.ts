import * as THREE from 'three';

/**
 * Off-graph storage for the LOD children a `gltf-lod-root` is not currently
 * showing.
 *
 * Hiding an inactive LOD child with `visible = false` still leaves it hanging
 * off the scene: `Object3D.updateMatrixWorld` ignores visibility and walks it
 * every frame, and a rigged prop's LOD child brings its whole skeleton along
 * (simple-rpg: ~141 bones per level, thousands of nodes across the map, for
 * geometry that is never drawn). Detaching the inactive levels is what actually
 * removes them from the per-frame walk.
 *
 * The levels stay reachable here, keyed by root, so the LOD switch can put one
 * back in place, and so disposal can still find every clone a root ever owned.
 */
interface LodParking {
  /** Every LOD child registered for the root, keyed by level. */
  children: Map<number, THREE.Object3D>;
  /** Level currently attached to the root (`-1` before the first attach). */
  activeLevel: number;
}

const parkingByRoot = new WeakMap<THREE.Object3D, LodParking>();

function getParking(root: THREE.Object3D): LodParking | undefined {
  return parkingByRoot.get(root);
}

function ensureParking(root: THREE.Object3D): LodParking {
  let parking = parkingByRoot.get(root);
  if (!parking) {
    parking = { children: new Map(), activeLevel: -1 };
    parkingByRoot.set(root, parking);
  }
  return parking;
}

/**
 * Hand a freshly cloned LOD child to the registry. Call *after* the child is
 * attached and its materials are set up — the level that is not active gets
 * detached right here, so nothing downstream has to know about parking.
 */
export function registerLodChild(
  root: THREE.Object3D,
  child: THREE.Object3D,
  level: number
): void {
  const parking = ensureParking(root);
  parking.children.set(level, child);
  if (parking.activeLevel === -1) parking.activeLevel = level;
  setActiveLodLevel(root, parking.activeLevel);
}

/** True when `level` was already registered (guards double-attach on retries). */
export function hasLodChild(root: THREE.Object3D, level: number): boolean {
  return getParking(root)?.children.has(level) === true;
}

/** The registered child for `level`, attached or parked. */
export function getLodChild(
  root: THREE.Object3D,
  level: number
): THREE.Object3D | undefined {
  return getParking(root)?.children.get(level);
}

/**
 * Number of LOD levels this root owns — attached plus parked. Callers use it
 * where `root.children.length` used to serve, which now only ever counts the
 * single attached level.
 */
export function lodChildCount(root: THREE.Object3D): number {
  return getParking(root)?.children.size ?? root.children.length;
}

/** Level currently attached, or `-1` when the root has no LOD children yet. */
export function getActiveLodLevel(root: THREE.Object3D): number {
  return getParking(root)?.activeLevel ?? -1;
}

/**
 * Attach `level` and park every other registered level. Idempotent and cheap
 * to re-call: the common case is one map lookup and an integer compare.
 */
export function setActiveLodLevel(root: THREE.Object3D, level: number): void {
  const parking = getParking(root);
  if (!parking) return;
  const wanted = parking.children.has(level) ? level : parking.activeLevel;

  for (const [childLevel, child] of parking.children) {
    if (childLevel === wanted) {
      if (child.parent !== root) {
        root.add(child);
        // The child kept its local transform while parked; re-attaching does
        // not mark the world matrix stale on its own.
        child.matrixWorldNeedsUpdate = true;
      }
      if (!child.visible) child.visible = true;
    } else if (child.parent === root) {
      root.remove(child);
    }
  }

  parking.activeLevel = wanted;
}

/** Run `fn` over every LOD child of the root, parked ones included. */
export function forEachLodChild(
  root: THREE.Object3D,
  fn: (child: THREE.Object3D, level: number) => void
): void {
  const parking = getParking(root);
  if (!parking) return;
  for (const [level, child] of parking.children) fn(child, level);
}

/** Drop the registry entry (entity destroyed / root discarded). */
export function clearLodParking(root: THREE.Object3D): void {
  parkingByRoot.delete(root);
}
