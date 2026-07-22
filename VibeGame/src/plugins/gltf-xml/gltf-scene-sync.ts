import * as THREE from 'three';
import { defineSystem, type State, type System } from '../../core';
import { TransformHierarchySystem } from '../transforms';
import { Transform, WorldTransform } from '../transforms/components';
import { GltfPending } from './components';
import {
  forEachGltfRootGroup,
  pruneStaleGltfRootGroups,
} from './group-registry';

/**
 * Mantém o `Group` Three.js do GLB alinhado ao `Transform` / `WorldTransform` em ECS.
 * Obrigatório para `gltf-dynamic`: a física move o `Rigidbody` → `Transform`; sem isto o mesh
 * fica na posição inicial e o jogador atravessa o modelo embora o colisor Rapier se mova.
 */

interface AppliedGltfPose {
  posX: number;
  posY: number;
  posZ: number;
  sx: number;
  sy: number;
  sz: number;
  rx: number;
  ry: number;
  rz: number;
  rw: number;
  eulerX: number;
  eulerY: number;
  eulerZ: number;
  usedQuat: boolean;
}

const appliedGltfPose = new WeakMap<THREE.Object3D, AppliedGltfPose>();

function applyWorldLikeTransformToGroup(
  group: THREE.Object3D,
  eid: number,
  state: State
): void {
  const useWorld = state.hasComponent(eid, WorldTransform);
  const posX = useWorld ? WorldTransform.posX[eid] : Transform.posX[eid];
  const posY = useWorld ? WorldTransform.posY[eid] : Transform.posY[eid];
  const posZ = useWorld ? WorldTransform.posZ[eid] : Transform.posZ[eid];
  const sx = useWorld ? WorldTransform.scaleX[eid] : Transform.scaleX[eid];
  const sy = useWorld ? WorldTransform.scaleY[eid] : Transform.scaleY[eid];
  const sz = useWorld ? WorldTransform.scaleZ[eid] : Transform.scaleZ[eid];
  const rx = useWorld ? WorldTransform.rotX[eid] : Transform.rotX[eid];
  const ry = useWorld ? WorldTransform.rotY[eid] : Transform.rotY[eid];
  const rz = useWorld ? WorldTransform.rotZ[eid] : Transform.rotZ[eid];
  const rw = useWorld ? WorldTransform.rotW[eid] : Transform.rotW[eid];
  const eulerX = useWorld ? WorldTransform.eulerX[eid] : Transform.eulerX[eid];
  const eulerY = useWorld ? WorldTransform.eulerY[eid] : Transform.eulerY[eid];
  const eulerZ = useWorld ? WorldTransform.eulerZ[eid] : Transform.eulerZ[eid];

  const quatIdentity =
    Math.abs(rw - 1) < 1e-6 &&
    Math.abs(rx) < 1e-6 &&
    Math.abs(ry) < 1e-6 &&
    Math.abs(rz) < 1e-6;

  const prev = appliedGltfPose.get(group);
  if (
    prev &&
    prev.posX === posX &&
    prev.posY === posY &&
    prev.posZ === posZ &&
    prev.sx === sx &&
    prev.sy === sy &&
    prev.sz === sz &&
    prev.usedQuat === !quatIdentity &&
    (quatIdentity
      ? prev.eulerX === eulerX &&
        prev.eulerY === eulerY &&
        prev.eulerZ === eulerZ
      : prev.rx === rx && prev.ry === ry && prev.rz === rz && prev.rw === rw)
  ) {
    return;
  }

  group.position.set(posX, posY, posZ);
  group.scale.set(sx, sy, sz);
  if (quatIdentity) {
    group.rotation.set(eulerX, eulerY, eulerZ);
  } else {
    group.quaternion.set(rx, ry, rz, rw);
  }

  if (!prev) {
    appliedGltfPose.set(group, {
      posX,
      posY,
      posZ,
      sx,
      sy,
      sz,
      rx,
      ry,
      rz,
      rw,
      eulerX,
      eulerY,
      eulerZ,
      usedQuat: !quatIdentity,
    });
  } else {
    prev.posX = posX;
    prev.posY = posY;
    prev.posZ = posZ;
    prev.sx = sx;
    prev.sy = sy;
    prev.sz = sz;
    prev.rx = rx;
    prev.ry = ry;
    prev.rz = rz;
    prev.rw = rw;
    prev.eulerX = eulerX;
    prev.eulerY = eulerY;
    prev.eulerZ = eulerZ;
    prev.usedQuat = !quatIdentity;
  }
}

export const GltfSceneSyncSystem: System = defineSystem({
  name: 'GltfSceneSyncSystem',
  group: 'simulation',
  after: [TransformHierarchySystem],
  update(state) {
    if (state.headless) return;

    pruneStaleGltfRootGroups(state);

    forEachGltfRootGroup(state, (eid, group) => {
      if (!state.exists(eid)) return;
      if (!GltfPending.loaded[eid]) return;
      if (!state.hasComponent(eid, Transform)) return;

      applyWorldLikeTransformToGroup(group, eid, state);
    });
  },
});
