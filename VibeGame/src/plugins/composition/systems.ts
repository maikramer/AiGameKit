import * as THREE from 'three';
import { defineSystem, defineQuery } from '../../core';
import type { State, System } from '../../core';
import {
  getBodyForEntity,
  getRapierWorld,
  PhysicsInitializationSystem,
} from '../physics/systems';
import { getScene, setupCsmMaterials } from '../rendering';
import { TransformHierarchySystem } from '../transforms';
import { Transform, WorldTransform } from '../transforms/components';
import { CompositionPending } from './components';
import {
  forEachCompositionGroup,
  pruneStaleCompositionGroups,
  registerCompositionGroup,
} from './group-registry';
import {
  buildPrimitiveColliderDesc,
  buildPrimitiveMesh,
  getCompositionData,
} from './primitives';

const compositionQuery = defineQuery([CompositionPending]);

function syncGroupPose(group: THREE.Object3D, eid: number, state: State): void {
  const useWorld = state.hasComponent(eid, WorldTransform);
  const t = useWorld ? WorldTransform : Transform;
  const px = t.posX[eid];
  const py = t.posY[eid];
  const pz = t.posZ[eid];
  const sx = t.scaleX[eid];
  const sy = t.scaleY[eid];
  const sz = t.scaleZ[eid];
  const rx = t.rotX[eid];
  const ry = t.rotY[eid];
  const rz = t.rotZ[eid];
  const rw = t.rotW[eid];
  const isIdentity =
    Math.abs(rw - 1) < 1e-6 &&
    Math.abs(rx) < 1e-6 &&
    Math.abs(ry) < 1e-6 &&
    Math.abs(rz) < 1e-6;
  const poseUnchanged =
    group.position.x === px &&
    group.position.y === py &&
    group.position.z === pz &&
    group.scale.x === sx &&
    group.scale.y === sy &&
    group.scale.z === sz &&
    (isIdentity
      ? group.rotation.x === t.eulerX[eid] &&
        group.rotation.y === t.eulerY[eid] &&
        group.rotation.z === t.eulerZ[eid]
      : group.quaternion.x === rx &&
        group.quaternion.y === ry &&
        group.quaternion.z === rz &&
        group.quaternion.w === rw);
  if (poseUnchanged) return;

  group.position.set(px, py, pz);
  group.scale.set(sx, sy, sz);
  if (isIdentity) {
    group.rotation.set(t.eulerX[eid], t.eulerY[eid], t.eulerZ[eid]);
  } else {
    group.quaternion.set(rx, ry, rz, rw);
  }
}

export const CompositionSetupSystem: System = defineSystem({
  name: 'CompositionSetupSystem',
  group: 'setup',
  update(state) {
    if (state.headless) return;
    const scene = getScene(state);
    if (!scene) return;

    for (const eid of compositionQuery(state.world)) {
      if (CompositionPending.meshBuilt[eid]) continue;
      const data = getCompositionData(state, eid);
      if (!data) {
        CompositionPending.meshBuilt[eid] = 1;
        continue;
      }

      const group = new THREE.Group();
      for (const spec of data.specs) {
        group.add(buildPrimitiveMesh(spec));
      }
      syncGroupPose(group, eid, state);
      scene.add(group);
      setupCsmMaterials(state, group);
      registerCompositionGroup(state, eid, group);
      CompositionPending.meshBuilt[eid] = 1;
    }
  },
});

// Compound colliders attach post-physics-init because the Collider component is
// SOA one-row-per-entity; multiple colliders per body need direct Rapier calls.
export const CompositionColliderSystem: System = defineSystem({
  name: 'CompositionColliderSystem',
  group: 'fixed',
  after: [PhysicsInitializationSystem],
  update(state) {
    for (const eid of compositionQuery(state.world)) {
      if (CompositionPending.colliderBuilt[eid]) continue;
      const data = getCompositionData(state, eid);
      if (!data) {
        CompositionPending.colliderBuilt[eid] = 1;
        continue;
      }
      if (data.colliderMode === 'none') {
        CompositionPending.colliderBuilt[eid] = 1;
        continue;
      }

      const body = getBodyForEntity(state, eid);
      if (!body) continue; // body not yet created (placement pending / physics hold)
      const world = getRapierWorld(state);
      if (!world) continue;

      const scaleX = Transform.scaleX[eid] || 1;
      const scaleY = Transform.scaleY[eid] || 1;
      const scaleZ = Transform.scaleZ[eid] || 1;

      for (const spec of data.specs) {
        const desc = buildPrimitiveColliderDesc(spec, scaleX, scaleY, scaleZ);
        world.createCollider(desc, body);
      }
      CompositionPending.colliderBuilt[eid] = 1;
    }
  },
});

export const CompositionSyncSystem: System = defineSystem({
  name: 'CompositionSyncSystem',
  group: 'simulation',
  after: [TransformHierarchySystem],
  update(state) {
    if (state.headless) return;
    pruneStaleCompositionGroups(state);
    forEachCompositionGroup(state, (eid, group) => {
      if (!state.exists(eid)) return;
      if (!state.hasComponent(eid, Transform)) return;
      syncGroupPose(group, eid, state);
    });
  },
});
