import * as RAPIER from '@dimforge/rapier3d-compat';
import * as THREE from 'three';
import { defineQuery, type System } from '../../core';
import { PhysicsInterpolationSystem } from '../physics/systems';
import { RaycastResult, RaycastSource } from './components';
import { castRapierRay, getRayOriginFromEntity } from './utils';

const rayQuery = defineQuery([RaycastSource, RaycastResult]);

const _origin = new THREE.Vector3();

/** Limpa resultados antes do resto do frame (setup corre primeiro). */
export const RaycastResetSystem: System = {
  group: 'setup',
  first: true,
  update: (state) => {
    for (const eid of rayQuery(state.world)) {
      RaycastResult.hitValid[eid] = 0;
    }
  },
};

export const RaycastSystem: System = {
  group: 'simulation',
  after: [PhysicsInterpolationSystem],
  update: (state) => {
    for (const eid of rayQuery(state.world)) {
      getRayOriginFromEntity(state, eid, _origin);
      const dx = RaycastSource.dirX[eid];
      const dy = RaycastSource.dirY[eid];
      const dz = RaycastSource.dirZ[eid];
      const len = Math.hypot(dx, dy, dz) || 1;
      const ndx = dx / len;
      const ndy = dy / len;
      const ndz = dz / len;

      const maxDist = RaycastSource.maxDist[eid];
      const layerMask = RaycastSource.layerMask[eid];

      const origin = new RAPIER.Vector3(_origin.x, _origin.y, _origin.z);
      const dir = new RAPIER.Vector3(ndx, ndy, ndz);

      const hit = castRapierRay(state, origin, dir, maxDist, layerMask);
      if (!hit) {
        RaycastResult.hitValid[eid] = 0;
        continue;
      }

      RaycastResult.hitValid[eid] = 1;
      RaycastResult.hitEntity[eid] = hit.entity;
      RaycastResult.hitDist[eid] = hit.toi;
      RaycastResult.hitNormalX[eid] = hit.normal.x;
      RaycastResult.hitNormalY[eid] = hit.normal.y;
      RaycastResult.hitNormalZ[eid] = hit.normal.z;
      RaycastResult.hitPointX[eid] = hit.point.x;
      RaycastResult.hitPointY[eid] = hit.point.y;
      RaycastResult.hitPointZ[eid] = hit.point.z;
    }
  },
};
