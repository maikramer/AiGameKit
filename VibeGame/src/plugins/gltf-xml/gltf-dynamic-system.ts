import * as THREE from 'three';
import { defineQuery, type System } from '../../core';
import { Rigidbody, BodyType, Collider } from '../physics/components';
import { syncBodyQuaternionFromEuler } from '../physics/utils';
import { Transform } from '../transforms/components';
import { GltfLod, GltfPending, GltfPhysicsPending } from './components';
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

      const lodMid =
        state.hasComponent(eid, GltfLod) && group.children.length >= 2
          ? (group.children[1] as THREE.Object3D)
          : group;
      lodMid.updateMatrixWorld(true);
      _box.setFromObject(lodMid);
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

      if (!state.hasComponent(eid, Rigidbody)) {
        state.addComponent(eid, Rigidbody);
      }
      if (!state.hasComponent(eid, Collider)) {
        state.addComponent(eid, Collider);
      }

      const bodyType = GltfPhysicsPending.bodyType[eid];

      Rigidbody.type[eid] = bodyType;
      if (bodyType !== BodyType.Fixed) {
        Rigidbody.mass[eid] = GltfPhysicsPending.mass[eid];
      }
      Rigidbody.gravityScale[eid] = 1;
      Rigidbody.posX[eid] = Transform.posX[eid];
      Rigidbody.posY[eid] = Transform.posY[eid];
      Rigidbody.posZ[eid] = Transform.posZ[eid];
      Rigidbody.eulerX[eid] = Transform.eulerX[eid];
      Rigidbody.eulerY[eid] = Transform.eulerY[eid];
      Rigidbody.eulerZ[eid] = Transform.eulerZ[eid];
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
        Rigidbody.rotX[eid],
        Rigidbody.rotY[eid],
        Rigidbody.rotZ[eid],
        Rigidbody.rotW[eid]
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

      if (bodyType !== BodyType.Fixed) {
        Rigidbody.ccd[eid] = 1;
        Rigidbody.linearDamping[eid] = 0.2;
        Rigidbody.angularDamping[eid] = 0.4;
      }

      GltfPhysicsPending.ready[eid] = 1;
    }
  },
};
