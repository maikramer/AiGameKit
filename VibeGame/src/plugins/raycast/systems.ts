import * as RAPIER from '@dimforge/rapier3d-compat';
import * as THREE from 'three';
import { defineQuery, type State, type System } from '../../core';
import { forEachGltfRootGroup } from '../gltf-xml/group-registry';
import { PhysicsInterpolationSystem } from '../physics/systems';
import { getRenderingContext } from '../rendering/utils';
import { RaycastResult, RaycastSource } from './components';
import { castRapierRay, getRayOriginFromEntity } from './utils';

const rayQuery = defineQuery([RaycastSource, RaycastResult]);

const _origin = new THREE.Vector3();
const _raycaster = new THREE.Raycaster();
const _dir = new THREE.Vector3();
const _normal = new THREE.Vector3();
const _groupToEntity = new Map<THREE.Object3D, number>();

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

/**
 * Resolve entity a partir de um Object3D atingido pelo raycast.
 * Percorre a cadeia de parents até encontrar:
 *   - um Group registado no group-registry (GLTF models)
 *   - um InstancedMesh presente em entityInstances (primitive renderers)
 */
function resolveEntityFromObject(
  state: State,
  object: THREE.Object3D
): number | null {
  const renderCtx = getRenderingContext(state);

  let current: THREE.Object3D | null = object;
  while (current) {
    if (_groupToEntity.has(current)) {
      return _groupToEntity.get(current)!;
    }

    if (current instanceof THREE.InstancedMesh) {
      for (const [entity, info] of renderCtx.entityInstances) {
        const pools = info.unlit
          ? renderCtx.unlitMeshPools
          : renderCtx.meshPools;
        if (pools.get(info.poolId) === current) {
          return entity;
        }
      }
    }
    current = current.parent;
  }

  return null;
}

function castBvhRay(
  state: State,
  origin: THREE.Vector3,
  direction: THREE.Vector3,
  maxDist: number
): {
  entity: number;
  distance: number;
  normal: THREE.Vector3;
  point: THREE.Vector3;
} | null {
  _groupToEntity.clear();
  forEachGltfRootGroup(state, (entity, group) => {
    _groupToEntity.set(group, entity);
  });

  const scene = getRenderingContext(state).scene;

  _raycaster.set(origin, direction);
  _raycaster.near = 0;
  _raycaster.far = maxDist;

  const hits = _raycaster.intersectObjects(scene.children, true);
  if (hits.length === 0) return null;

  for (const hit of hits) {
    const entity = resolveEntityFromObject(state, hit.object);
    if (entity !== null) {
      if (hit.face) {
        _normal
          .copy(hit.face.normal)
          .transformDirection(hit.object.matrixWorld);
      } else {
        _normal.set(0, 1, 0);
      }
      return {
        entity,
        distance: hit.distance,
        normal: _normal,
        point: hit.point,
      };
    }
  }

  return null;
}

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
        const hit = castBvhRay(state, _origin, _dir, maxDist);
        if (!hit) {
          RaycastResult.hitValid[eid] = 0;
          continue;
        }

        RaycastResult.hitValid[eid] = 1;
        RaycastResult.hitEntity[eid] = hit.entity;
        RaycastResult.hitDist[eid] = hit.distance;
        RaycastResult.hitNormalX[eid] = hit.normal.x;
        RaycastResult.hitNormalY[eid] = hit.normal.y;
        RaycastResult.hitNormalZ[eid] = hit.normal.z;
        RaycastResult.hitPointX[eid] = hit.point.x;
        RaycastResult.hitPointY[eid] = hit.point.y;
        RaycastResult.hitPointZ[eid] = hit.point.z;
        continue;
      }

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
