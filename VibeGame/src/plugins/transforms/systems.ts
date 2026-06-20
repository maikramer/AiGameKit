import * as THREE from 'three';
import { System, defineQuery } from '../../core';
import type { State } from '../../core';
import { Parent } from '../../core';
import { Transform, WorldTransform } from './components';
import {
  composeTransformMatrix,
  copyTransform,
  decomposeTransformMatrix,
  syncEulerFromQuaternion,
  syncQuaternionFromEuler,
} from './utils';

const matrix = new THREE.Matrix4();
const parentMatrix = new THREE.Matrix4();
const position = new THREE.Vector3();
const rotation = new THREE.Quaternion();
const scale = new THREE.Vector3();

const transformQuery = defineQuery([Transform]);

function parentIsDirty(state: State, entity: number): boolean {
  if (!state.hasComponent(entity, Parent)) return false;
  const parent = Parent.entity[entity];
  return Transform.dirty[parent] === 1;
}

export const TransformHierarchySystem: System = {
  group: 'simulation',
  last: true,
  update: (state) => {
    const entities = transformQuery(state.world);

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
      if (!isDirty && !parentIsDirty(state, entity)) continue;

      syncQuaternionFromEuler(Transform, entity);

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

      if (!state.hasComponent(entity, Parent)) {
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

      if (
        state.hasComponent(entity, Parent) &&
        state.hasComponent(entity, WorldTransform)
      ) {
        syncEulerFromQuaternion(WorldTransform, entity);
      }
    }

    // Deferred dirty-clear: runs only after every entity has been processed,
    // so children evaluated later in the pass still observe their parent's
    // dirty flag as set.
    for (const entity of entities) {
      if (Transform.dirty[entity] === 1) {
        Transform.dirty[entity] = 0;
      }
    }
  },
};
