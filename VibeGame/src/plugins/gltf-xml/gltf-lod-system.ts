import * as THREE from 'three';
import { defineQuery, type System } from '../../core';
import { MainCamera } from '../rendering/components';
import { CameraSyncSystem } from '../rendering/systems';
import { Transform, WorldTransform } from '../transforms/components';
import { GltfLod, GltfPending } from './components';
import { getGltfRootGroup } from './group-registry';
import { pickLodLevel } from './gltf-lod-level';

const lodQuery = defineQuery([GltfLod, GltfPending, Transform]);
const cameraQuery = defineQuery([MainCamera, WorldTransform]);

const _objPos = new THREE.Vector3();

export const GltfLodSystem: System = {
  group: 'draw',
  after: [CameraSyncSystem],
  update(state) {
    if (state.headless) return;

    const cams = cameraQuery(state.world);
    if (cams.length === 0) return;
    const camEid = cams[0];
    const cx = WorldTransform.posX[camEid];
    const cy = WorldTransform.posY[camEid];
    const cz = WorldTransform.posZ[camEid];

    for (const eid of lodQuery(state.world)) {
      if (GltfPending.loaded[eid] !== 1) continue;

      const root = getGltfRootGroup(state, eid);
      const childCount = root?.children.length ?? 0;
      if (!root || childCount < 2) continue;

      const useWorld = state.hasComponent(eid, WorldTransform);
      const ox = useWorld ? WorldTransform.posX[eid] : Transform.posX[eid];
      const oy = useWorld ? WorldTransform.posY[eid] : Transform.posY[eid];
      const oz = useWorld ? WorldTransform.posZ[eid] : Transform.posZ[eid];
      _objPos.set(ox, oy, oz);
      const dx = _objPos.x - cx;
      const dy = _objPos.y - cy;
      const dz = _objPos.z - cz;
      const dist = Math.sqrt(dx * dx + dy * dy + dz * dz);

      const near = GltfLod.thresholdNear[eid];
      const mid = GltfLod.thresholdMid[eid];
      const raw = pickLodLevel(dist, near, mid);
      const level = Math.min(raw, childCount - 1);
      if (level === GltfLod.activeLevel[eid]) {
        continue;
      }
      GltfLod.activeLevel[eid] = level;

      for (let i = 0; i < root.children.length; i++) {
        const ch = root.children[i];
        ch.visible = i === level;
      }
    }
  },
};
