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
const _lastLodCam = { x: Number.NaN, y: Number.NaN, z: Number.NaN };
const LOD_CAM_STILL_EPS_SQ = 0.01; // ~0.1 m

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

    const camStill =
      (cx - _lastLodCam.x) ** 2 +
        (cy - _lastLodCam.y) ** 2 +
        (cz - _lastLodCam.z) ** 2 <
      LOD_CAM_STILL_EPS_SQ;
    _lastLodCam.x = cx;
    _lastLodCam.y = cy;
    _lastLodCam.z = cz;

    for (const eid of lodQuery(state.world)) {
      if (GltfPending.loaded[eid] !== 1) continue;

      // Camera barely moved: settled LODs stay valid — skip dist + child scan.
      if (camStill && GltfLod.settled[eid] === 1) continue;

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

      const farCutoff = mid * 1.15;
      if (dist > farCutoff) {
        root.visible = false;
        continue;
      }
      root.visible = true;

      const prevLevel = GltfLod.activeLevel[eid];
      const raw = pickLodLevel(dist, near, mid, prevLevel);
      const level = Math.min(raw, childCount - 1);
      if (level === prevLevel && GltfLod.settled[eid] === 1) {
        continue;
      }
      if (level === prevLevel) {
        // Load-time: GLTF may arrive with all LOD children visible. One scan
        // to confirm a single visible child, then mark settled.
        let visibleCount = 0;
        for (let i = 0; i < childCount; i++) {
          if (root.children[i].visible) visibleCount++;
        }
        if (visibleCount === 1) {
          GltfLod.settled[eid] = 1;
          continue;
        }
      }
      GltfLod.activeLevel[eid] = level;
      GltfLod.settled[eid] = 1;

      for (let i = 0; i < root.children.length; i++) {
        root.children[i].visible = i === level;
      }
    }
  },
};
