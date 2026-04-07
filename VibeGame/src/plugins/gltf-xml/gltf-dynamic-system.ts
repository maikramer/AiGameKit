import * as THREE from 'three';
import { defineQuery, type System } from '../../core';
import { Body, BodyType, Collider } from '../physics/components';
import { syncBodyQuaternionFromEuler } from '../physics/utils';
import { Transform } from '../transforms/components';
import { GltfPending, GltfPhysicsPending } from './components';
import { fitColliderFromAabb } from './gltf-dynamic-collider-fit';
import { deleteGltfRootGroup, getGltfRootGroup } from './group-registry';
import { GltfXmlLoadSystem } from './systems';

const query = defineQuery([GltfPhysicsPending, GltfPending, Transform]);

const _box = new THREE.Box3();
const _size = new THREE.Vector3();
const _aabbCenterWorld = new THREE.Vector3();
const _deltaWorld = new THREE.Vector3();
const _bodyQuat = new THREE.Quaternion();
const _invBodyQuat = new THREE.Quaternion();
const _offsetLocal = new THREE.Vector3();

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

      // Centro do AABB em mundo vs origem do grupo (Transform): sem isto o colisor fica
      // desalinhado do mesh e o jogador pode "atravessar" o modelo visível.
      _box.getCenter(_aabbCenterWorld);
      _deltaWorld.set(
        _aabbCenterWorld.x - Transform.posX[eid],
        _aabbCenterWorld.y - Transform.posY[eid],
        _aabbCenterWorld.z - Transform.posZ[eid]
      );
      _bodyQuat.set(
        Body.rotX[eid],
        Body.rotY[eid],
        Body.rotZ[eid],
        Body.rotW[eid]
      );
      _invBodyQuat.copy(_bodyQuat).invert();
      _offsetLocal.copy(_deltaWorld).applyQuaternion(_invBodyQuat);

      const fit = fitColliderFromAabb(
        GltfPhysicsPending.colliderShape[eid],
        sx,
        sy,
        sz,
        tsx,
        tsy,
        tsz
      );
      Collider.shape[eid] = fit.shape;
      Collider.sizeX[eid] = fit.sizeX;
      Collider.sizeY[eid] = fit.sizeY;
      Collider.sizeZ[eid] = fit.sizeZ;
      Collider.radius[eid] = fit.radius;
      Collider.height[eid] = fit.height;
      Collider.friction[eid] = GltfPhysicsPending.friction[eid];
      Collider.restitution[eid] = GltfPhysicsPending.restitution[eid];
      Collider.density[eid] = 1;
      Collider.isSensor[eid] = 0;
      Collider.membershipGroups[eid] = 0xffff;
      Collider.filterGroups[eid] = 0xffff;
      Collider.posOffsetX[eid] = _offsetLocal.x;
      Collider.posOffsetY[eid] = _offsetLocal.y;
      Collider.posOffsetZ[eid] = _offsetLocal.z;
      Collider.rotOffsetW[eid] = 1;

      Body.ccd[eid] = 1;
      Body.linearDamping[eid] = 0.2;
      Body.angularDamping[eid] = 0.4;

      GltfPhysicsPending.ready[eid] = 1;
    }
  },
};
