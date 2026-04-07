import * as THREE from 'three';
import { defineQuery, type System } from '../../core';
import {
  Body,
  BodyType,
  Collider,
  ColliderShape,
} from '../physics/components';
import { syncBodyQuaternionFromEuler } from '../physics/utils';
import { Transform } from '../transforms/components';
import { GltfPending, GltfPhysicsPending } from './components';
import { deleteGltfRootGroup, getGltfRootGroup } from './group-registry';
import { GltfXmlLoadSystem } from './systems';

const query = defineQuery([GltfPhysicsPending, GltfPending, Transform]);

const _box = new THREE.Box3();
const _size = new THREE.Vector3();

const MIN_HALF_DIM = 0.05;

export const GltfDynamicPhysicsSystem: System = {
  group: 'setup',
  after: [GltfXmlLoadSystem],
  update(state) {
    if (state.headless) return;

    for (const eid of query(state.world)) {
      if (GltfPhysicsPending.ready[eid]) continue;
      if (!GltfPending.loaded[eid]) continue;
      if (!state.exists(eid)) continue;

      const group = getGltfRootGroup(state, eid);
      if (!group) {
        continue;
      }

      group.updateMatrixWorld(true);
      _box.setFromObject(group);
      if (_box.isEmpty()) {
        console.warn(
          `[gltf-dynamic] AABB vazio para entidade ${eid}; física omitida.`
        );
        GltfPhysicsPending.ready[eid] = 1;
        deleteGltfRootGroup(state, eid);
        continue;
      }

      _box.getSize(_size);
      const margin = GltfPhysicsPending.colliderMargin[eid];
      let sx = _size.x + 2 * margin;
      let sy = _size.y + 2 * margin;
      let sz = _size.z + 2 * margin;
      sx = Math.max(sx, MIN_HALF_DIM * 2);
      sy = Math.max(sy, MIN_HALF_DIM * 2);
      sz = Math.max(sz, MIN_HALF_DIM * 2);

      const tsx = Math.max(Math.abs(Transform.scaleX[eid]), 1e-6);
      const tsy = Math.max(Math.abs(Transform.scaleY[eid]), 1e-6);
      const tsz = Math.max(Math.abs(Transform.scaleZ[eid]), 1e-6);

      if (!state.hasComponent(eid, Body)) {
        state.addComponent(eid, Body);
      }
      if (!state.hasComponent(eid, Collider)) {
        state.addComponent(eid, Collider);
      }

      Body.type[eid] = BodyType.Dynamic;
      Body.mass[eid] = GltfPhysicsPending.mass[eid];
      Body.gravityScale[eid] = 1;
      Body.posX[eid] = Transform.posX[eid];
      Body.posY[eid] = Transform.posY[eid];
      Body.posZ[eid] = Transform.posZ[eid];
      Body.eulerX[eid] = Transform.eulerX[eid];
      Body.eulerY[eid] = Transform.eulerY[eid];
      Body.eulerZ[eid] = Transform.eulerZ[eid];
      syncBodyQuaternionFromEuler(eid);

      Collider.shape[eid] = ColliderShape.Box;
      Collider.sizeX[eid] = sx / tsx;
      Collider.sizeY[eid] = sy / tsy;
      Collider.sizeZ[eid] = sz / tsz;
      Collider.friction[eid] = GltfPhysicsPending.friction[eid];
      Collider.restitution[eid] = GltfPhysicsPending.restitution[eid];

      GltfPhysicsPending.ready[eid] = 1;
      deleteGltfRootGroup(state, eid);
    }
  },
};
