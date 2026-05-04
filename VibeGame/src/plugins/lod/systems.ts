import { defineQuery, type System } from '../../core';
import { MainCamera } from '../rendering/components';
import { WorldTransform } from '../transforms';
import { LODGroup } from './components';

const lodQuery = defineQuery([LODGroup, WorldTransform]);

const mainCameraQuery = defineQuery([MainCamera, WorldTransform]);
const fallbackCameraQuery = defineQuery([WorldTransform]);

export const LodSystem: System = {
  group: 'simulation',
  update(state) {
    const lodEntities = lodQuery(state.world);
    if (lodEntities.length === 0) return;

    const mainCameras = mainCameraQuery(state.world);
    const camEid =
      mainCameras.length > 0
        ? mainCameras[0]
        : fallbackCameraQuery(state.world)[0];
    if (camEid === undefined) return;

    const cx = WorldTransform.posX[camEid];
    const cy = WorldTransform.posY[camEid];
    const cz = WorldTransform.posZ[camEid];

    for (const eid of lodEntities) {
      const ex = WorldTransform.posX[eid];
      const ey = WorldTransform.posY[eid];
      const ez = WorldTransform.posZ[eid];

      const dx = ex - cx;
      const dy = ey - cy;
      const dz = ez - cz;
      const dist = Math.sqrt(dx * dx + dy * dy + dz * dz);

      const far = LODGroup.far[eid];
      const newLevel = dist > far ? 1 : 0;

      if (newLevel !== LODGroup.currentLevel[eid]) {
        LODGroup.currentLevel[eid] = newLevel;
      }
    }
  },
};
