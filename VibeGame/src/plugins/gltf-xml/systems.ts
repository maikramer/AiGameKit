import * as THREE from 'three';
import { defineQuery, type System } from '../../core';
import { loadGltfToScene } from '../../extras/gltf-bridge';
import { getScene } from '../rendering';
import { Transform } from '../transforms/components';
import { GltfPending, GltfPhysicsPending } from './components';
import { registerGltfLocalYBounds } from './gltf-bounds-cache';
import { getGltfUrl, isGltfInFlight, setGltfInFlight } from './context';
import { registerGltfRootGroup } from './group-registry';

const gltfLoadQuery = defineQuery([GltfPending]);

function applyTransformToGroup(group: THREE.Object3D, eid: number): void {
  group.position.set(
    Transform.posX[eid],
    Transform.posY[eid],
    Transform.posZ[eid]
  );
  group.scale.set(
    Transform.scaleX[eid],
    Transform.scaleY[eid],
    Transform.scaleZ[eid]
  );
  const rx = Transform.rotX[eid];
  const ry = Transform.rotY[eid];
  const rz = Transform.rotZ[eid];
  const rw = Transform.rotW[eid];
  const quatIdentity =
    Math.abs(rw - 1) < 1e-6 &&
    Math.abs(rx) < 1e-6 &&
    Math.abs(ry) < 1e-6 &&
    Math.abs(rz) < 1e-6;
  if (quatIdentity) {
    group.rotation.set(
      Transform.eulerX[eid],
      Transform.eulerY[eid],
      Transform.eulerZ[eid]
    );
  } else {
    group.quaternion.set(rx, ry, rz, rw);
  }
}

export const GltfXmlLoadSystem: System = {
  group: 'setup',
  update: (state) => {
    const scene = getScene(state);
    if (!scene) return;

    for (const eid of gltfLoadQuery(state.world)) {
      if (GltfPending.loaded[eid]) {
        continue;
      }
      if (isGltfInFlight(state, eid)) {
        continue;
      }
      const url = getGltfUrl(state, eid);
      if (!url) {
        GltfPending.loaded[eid] = 1;
        if (state.hasComponent(eid, GltfPhysicsPending)) {
          GltfPhysicsPending.ready[eid] = 1;
        }
        continue;
      }
      setGltfInFlight(state, eid, true);
      void loadGltfToScene(state, url)
        .then((group) => {
          registerGltfLocalYBounds(url, group);
          applyTransformToGroup(group, eid);
          if (
            state.exists(eid) &&
            state.hasComponent(eid, GltfPhysicsPending)
          ) {
            registerGltfRootGroup(state, eid, group);
          }
        })
        .catch((err: unknown) => {
          console.error('[gltf-load]', err);
          if (
            state.exists(eid) &&
            state.hasComponent(eid, GltfPhysicsPending)
          ) {
            GltfPhysicsPending.ready[eid] = 1;
          }
        })
        .finally(() => {
          GltfPending.loaded[eid] = 1;
          setGltfInFlight(state, eid, false);
        });
    }
  },
};
