import * as THREE from 'three';
import type { State, System } from '../../core';
import { TransformHierarchySystem } from '../transforms';
import { Transform, WorldTransform } from '../transforms/components';
import { GltfPending } from './components';
import {
  forEachGltfRootGroup,
  pruneStaleGltfRootGroups,
} from './group-registry';

/**
 * Mantém o `Group` Three.js do GLB alinhado ao `Transform` / `WorldTransform` em ECS.
 * Obrigatório para `gltf-dynamic`: a física move o `Body` → `Transform`; sem isto o mesh
 * fica na posição inicial e o jogador atravessa o modelo embora o colisor Rapier se mova.
 */
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

  group.position.set(posX, posY, posZ);
  group.scale.set(sx, sy, sz);
  const quatIdentity =
    Math.abs(rw - 1) < 1e-6 &&
    Math.abs(rx) < 1e-6 &&
    Math.abs(ry) < 1e-6 &&
    Math.abs(rz) < 1e-6;
  if (quatIdentity) {
    group.rotation.set(eulerX, eulerY, eulerZ);
  } else {
    group.quaternion.set(rx, ry, rz, rw);
  }
}

export const GltfSceneSyncSystem: System = {
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
};
