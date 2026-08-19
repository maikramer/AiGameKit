import * as THREE from 'three';
import { defineSystem, System, defineQueryLive, Parent } from '../../core';
import type { State } from '../../core';
import { MAX_ENTITIES } from '../../core/ecs/constants';
import { Transform, WorldTransform } from './components';
import {
  composeTransformMatrix,
  copyTransform,
  decomposeTransformMatrix,
  recordRotationShadow,
  resolveRotationSource,
  syncEulerFromQuaternion,
  syncQuaternionFromEuler,
} from './utils';

const matrix = new THREE.Matrix4();
const parentMatrix = new THREE.Matrix4();
const position = new THREE.Vector3();
const rotation = new THREE.Quaternion();
const scale = new THREE.Vector3();

// Read-only membership: this system may add WorldTransform, never Transform.
const transformQuery = defineQueryLive([Transform]);
// Parented entities are a tiny minority of the world (a few thousand out of
// ~90k in simple-rpg), so their membership is mirrored into a byte per eid once
// per pass. Every `hasParent` test below is then a typed-array read instead of
// a `state.hasComponent` call (entityExists + bitecs lookup) — the single
// biggest cost of this system when the scene is large.
const parentedQuery = defineQueryLive([Parent]);
const hasParent = new Uint8Array(MAX_ENTITIES);
/** Indices currently set in {@link hasParent} — cleared instead of refilling. */
const markedParents: number[] = [];
/** Entities whose dirty flag this pass must clear (deferred, see below). */
const dirtyScratch: number[] = [];

function refreshParentBitmap(state: State): void {
  for (let i = 0; i < markedParents.length; i++) {
    hasParent[markedParents[i]] = 0;
  }
  markedParents.length = 0;
  for (const entity of parentedQuery(state.world)) {
    if (entity >= MAX_ENTITIES) continue;
    hasParent[entity] = 1;
    markedParents.push(entity);
  }
}

function ancestorIsDirty(entity: number): boolean {
  // Walk up the Parent chain: if any ancestor is dirty this frame, the entity's
  // WorldTransform must be recomposed. Checking only the immediate parent would
  // miss deep hierarchies (e.g. moving a root Group must cascade to its
  // grandchildren), and the dirty flag is kept set until the deferred clear
  // below so descendants processed later still observe it.
  let current = entity;
  while (hasParent[current] === 1) {
    current = Parent.entity[current];
    if (Transform.dirty[current] === 1) return true;
  }
  return false;
}

export const TransformHierarchySystem: System = defineSystem({
  name: 'TransformHierarchySystem',
  group: 'simulation',
  last: true,
  update: (state) => {
    const entities = transformQuery(state.world);
    refreshParentBitmap(state);
    const dirtyEntities = dirtyScratch;
    dirtyEntities.length = 0;

    // Single pass over entities. Phases run per-entity in this order:
    //   1. sync Transform quaternion from its Euler (needs the entity's own
    //      dirty flag, set when its rotation changed).
    //   2. compose parent*local into WorldTransform.
    //   3. sync WorldTransform Euler from its quaternion.
    // Ordering constraints that prevent fully folding this into the loop:
    //   - Phase 2 for a child reads the parent's WorldTransform, so parents
    //     must be processed before children (guaranteed by entity creation
    //     order, same assumption as the previous multi-pass version).
    //   - The dirty flag is NOT cleared here: parent->child propagation relies
    //     on `parentIsDirty` staying true for the whole pass, so clearing is
    //     deferred to the second loop below.
    for (const entity of entities) {
      const isDirty = Transform.dirty[entity] === 1;
      if (isDirty) {
        dirtyEntities.push(entity);
      } else {
        // Most entities are clean roots: two typed-array reads and out.
        if (hasParent[entity] === 0) continue;
        if (!ancestorIsDirty(entity)) continue;
      }

      // Whoever wrote the rotation last wins — see `resolveRotationSource`.
      // Comparing the quaternion against the Euler cannot answer that question
      // (a mismatch looks the same from both sides), so the resolved pair is
      // remembered per entity and the side that moved since is the author.
      const source = resolveRotationSource(state, entity);
      if (source === 'quaternion') {
        syncEulerFromQuaternion(Transform, entity);
      } else if (source === 'euler') {
        syncQuaternionFromEuler(Transform, entity);
      }
      recordRotationShadow(state, entity);

      if (!state.hasComponent(entity, WorldTransform)) {
        state.addComponent(entity, WorldTransform);
        WorldTransform.rotX[entity] = 0;
        WorldTransform.rotY[entity] = 0;
        WorldTransform.rotZ[entity] = 0;
        WorldTransform.rotW[entity] = 1;
        WorldTransform.scaleX[entity] = 1;
        WorldTransform.scaleY[entity] = 1;
        WorldTransform.scaleZ[entity] = 1;
      }

      if (hasParent[entity] === 0) {
        copyTransform(Transform, WorldTransform, entity);
      } else {
        const parent = Parent.entity[entity];
        if (!state.hasComponent(parent, WorldTransform)) continue;

        composeTransformMatrix(
          WorldTransform,
          parent,
          parentMatrix,
          position,
          rotation,
          scale
        );
        composeTransformMatrix(
          Transform,
          entity,
          matrix,
          position,
          rotation,
          scale
        );

        parentMatrix.multiply(matrix);
        decomposeTransformMatrix(
          parentMatrix,
          WorldTransform,
          entity,
          position,
          rotation,
          scale
        );
      }

      // WorldTransform is guaranteed present here (added above, and the
      // parented branch `continue`s when the parent has none).
      syncEulerFromQuaternion(WorldTransform, entity);

      // Keep the local Euler in sync with the local quaternion so the next
      // Euler-first writer starts from the correct rotation. Otherwise a system
      // that wrote a quaternion (e.g. the vehicle controller) is overwritten on
      // the very next frame because `eulerX/Y/Z` still read 0,0,0.
    }

    // Deferred dirty-clear: runs only after every entity has been processed,
    // so children evaluated later in the pass still observe their parent's
    // dirty flag as set. Only the entities collected above are touched — a
    // second full sweep over every Transform costs more than the work itself
    // once a scene holds tens of thousands of static props.
    for (let i = 0; i < dirtyEntities.length; i++) {
      Transform.dirty[dirtyEntities[i]] = 0;
    }
  },
});
