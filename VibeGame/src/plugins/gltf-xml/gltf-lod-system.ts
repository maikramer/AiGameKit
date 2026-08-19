import * as THREE from 'three';
import { defineSystem, defineQuery, type System } from '../../core';
import { DistanceCull, MainCamera } from '../rendering/components';
import {
  isSubtreeMatrixFrozen,
  setSubtreeMatrixFrozen,
} from '../rendering/matrix-freeze';
import {
  getActiveLodLevel,
  lodChildCount,
  setActiveLodLevel,
} from '../../extras/gltf-lod-parking';
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

export const GltfLodSystem: System = defineSystem({
  name: 'GltfLodSystem',
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

      const root = getGltfRootGroup(state, eid);
      if (!root) continue;

      // Visibility every frame (before settled early-out). DistanceCull owns
      // show/hide via max-distance; LOD only picks which child mesh is active.
      if (state.hasComponent(eid, DistanceCull)) {
        const culled = DistanceCull.culled[eid] === 1;
        if (root.visible === culled) root.visible = !culled;
        // DistanceCullSystem freezes the subtree on its own flips, but a GLB
        // that finishes loading *after* the entity was already culled never saw
        // one — its root group did not exist yet. Reconcile here (the walk only
        // runs when the frozen state actually disagrees).
        if (isSubtreeMatrixFrozen(root) !== culled) {
          setSubtreeMatrixFrozen(root, culled);
        }
        if (culled) continue;
      } else if (!root.visible) {
        root.visible = true;
      }

      // Attached + parked: only the active level hangs off the root.
      const childCount = lodChildCount(root);
      if (childCount < 2) continue;

      // Camera barely moved: settled LODs stay valid — skip dist + child scan.
      if (camStill && GltfLod.settled[eid] === 1) continue;

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

      const prevLevel = GltfLod.activeLevel[eid];
      const raw = pickLodLevel(dist, near, mid, prevLevel);
      // Beyond mid threshold, keep the farthest LOD child visible.
      const level = Math.min(raw, childCount - 1);
      if (
        level === prevLevel &&
        GltfLod.settled[eid] === 1 &&
        getActiveLodLevel(root) === level
      ) {
        continue;
      }

      GltfLod.activeLevel[eid] = level;
      GltfLod.settled[eid] = 1;
      // Detaches every other level: a rigged prop's inactive LOD children keep
      // their own skeleton, and `updateMatrixWorld` walks hidden subtrees just
      // the same, so leaving them parented costs more per frame than the level
      // actually drawn (simple-rpg: ~11k bones across inactive LOD children).
      setActiveLodLevel(root, level);
    }
  },
});
