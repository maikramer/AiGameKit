import * as RAPIER from '@dimforge/rapier3d-compat';
import { hasComponent } from 'bitecs';
import * as THREE from 'three';
import type { State } from '../../core';
import { Collider as ColliderECS } from '../physics/components';
import { getPhysicsContext } from '../physics/systems';
import { Transform, WorldTransform } from '../transforms';

/**
 * Converte NDC (-1..1) num raio em espaço mundo (origem + direção normalizada).
 */
export function screenToWorldRay(
  camera: THREE.Camera,
  ndcX: number,
  ndcY: number,
  outOrigin: THREE.Vector3,
  outDir: THREE.Vector3
): void {
  const v = new THREE.Vector3(ndcX, ndcY, 0.5);
  v.unproject(camera);
  outOrigin.copy(camera.position);
  outDir.copy(v).sub(outOrigin).normalize();
}

/**
 * Raycast no mundo Rapier; devolve entidade ECS do primeiro collider atingido.
 */
export function castRapierRay(
  state: State,
  origin: RAPIER.Vector3,
  dir: RAPIER.Vector3,
  maxDist: number,
  layerMask: number
): {
  entity: number;
  toi: number;
  normal: RAPIER.Vector3;
  point: RAPIER.Vector3;
} | null {
  const context = getPhysicsContext(state);
  const world = context.physicsWorld;
  if (!world) return null;

  const ray = new RAPIER.Ray(origin, dir);
  const hit = world.castRayAndGetNormal(
    ray,
    maxDist,
    true,
    undefined,
    undefined,
    undefined,
    undefined,
    (collider) => {
      const entity = context.colliderToEntity.get(collider.handle);
      if (entity === undefined) return true;
      if (!state.hasComponent(entity, ColliderECS)) return false;
      const mem = ColliderECS.membershipGroups[entity] ?? 0xffff;
      return (mem & layerMask) === 0;
    }
  );

  if (!hit) return null;

  const entity = context.colliderToEntity.get(hit.collider.handle);
  if (entity === undefined) return null;

  const toi = hit.timeOfImpact;
  const point = ray.pointAt(toi);
  return {
    entity,
    toi,
    normal: hit.normal,
    point,
  };
}

/**
 * Origem do raio a partir da entidade (WorldTransform).
 */
export function getRayOriginFromEntity(
  state: State,
  eid: number,
  out: THREE.Vector3
): void {
  if (hasComponent(state.world, WorldTransform, eid)) {
    out.set(
      WorldTransform.posX[eid],
      WorldTransform.posY[eid],
      WorldTransform.posZ[eid]
    );
    return;
  }
  out.set(Transform.posX[eid], Transform.posY[eid], Transform.posZ[eid]);
}
