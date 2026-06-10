import * as THREE from 'three';
import { defineQuery, type System } from '../../core';
import { castBvhRay } from '../bvh/utils';
import { PhysicsInterpolationSystem } from '../physics/systems';
import { RaycastHit, RaycastSource } from './components';
import { castRapierRay, getRayOriginFromEntity } from './utils';

const rayQuery = defineQuery([RaycastSource, RaycastHit]);

const _origin = new THREE.Vector3();
const _dir = new THREE.Vector3();
const _rapierDir = { x: 0, y: 0, z: 0 };

/** Limpa resultados antes do resto do frame (setup corre primeiro). */
export const RaycastResetSystem: System = {
  group: 'setup',
  first: true,
  update: (state) => {
    for (const eid of rayQuery(state.world)) {
      RaycastHit.hitValid[eid] = 0;
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
      const mode = RaycastSource.mode[eid];

      if (mode === 1) {
        _dir.set(ndx, ndy, ndz);
        const hit = castBvhRay(state, _origin, _dir, maxDist, layerMask);
        if (!hit) {
          RaycastHit.hitValid[eid] = 0;
          continue;
        }

        RaycastHit.hitValid[eid] = 1;
        RaycastHit.hitEntity[eid] = hit.entity;
        RaycastHit.hitDist[eid] = hit.distance;
        RaycastHit.hitNormalX[eid] = hit.normal.x;
        RaycastHit.hitNormalY[eid] = hit.normal.y;
        RaycastHit.hitNormalZ[eid] = hit.normal.z;
        RaycastHit.hitPointX[eid] = hit.point.x;
        RaycastHit.hitPointY[eid] = hit.point.y;
        RaycastHit.hitPointZ[eid] = hit.point.z;
        continue;
      }

      _rapierDir.x = ndx;
      _rapierDir.y = ndy;
      _rapierDir.z = ndz;

      const hit = castRapierRay(state, _origin, _rapierDir, maxDist, layerMask);
      if (!hit) {
        RaycastHit.hitValid[eid] = 0;
        continue;
      }

      RaycastHit.hitValid[eid] = 1;
      RaycastHit.hitEntity[eid] = hit.entity;
      RaycastHit.hitDist[eid] = hit.toi;
      RaycastHit.hitNormalX[eid] = hit.normal.x;
      RaycastHit.hitNormalY[eid] = hit.normal.y;
      RaycastHit.hitNormalZ[eid] = hit.normal.z;
      RaycastHit.hitPointX[eid] = hit.point.x;
      RaycastHit.hitPointY[eid] = hit.point.y;
      RaycastHit.hitPointZ[eid] = hit.point.z;
    }
  },
};
