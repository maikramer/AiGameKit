import * as RAPIER from '@dimforge/rapier3d-compat';
import * as THREE from 'three';
import { NULL_ENTITY, type State } from '../../core';
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

const _ray = new RAPIER.Ray(
  new RAPIER.Vector3(0, 0, 0),
  new RAPIER.Vector3(0, 0, 0)
);

// Estado partilhado com o predicate estável (evita alocar uma closure por raio).
let _filterState: State;
let _filterContext: ReturnType<typeof getPhysicsContext>;
let _filterMask = 0xffff;

const _filterPredicate = (collider: RAPIER.Collider): boolean => {
  const entity = _filterContext.colliderToEntity.get(collider.handle);
  // Colliders do motor (e.g. heightfields do terreno) não têm entidade ECS;
  // são sempre atingíveis.
  if (entity === undefined) return true;
  if (!_filterState.hasComponent(entity, ColliderECS)) return false;
  const mem = ColliderECS.membershipGroups[entity] || 0xffff;
  return (mem & _filterMask) !== 0;
};

/**
 * Raycast no mundo Rapier; devolve entidade ECS do primeiro collider atingido.
 * Só considera colliders cuja membership sobrepõe `layerMask` (mesma semântica
 * do modo BVH). Hits em colliders do motor (terreno) devolvem
 * `entity === NULL_ENTITY` mas continuam válidos.
 */
export function castRapierRay(
  state: State,
  origin: { x: number; y: number; z: number },
  dir: { x: number; y: number; z: number },
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

  _ray.origin = origin;
  _ray.dir = dir;
  _filterState = state;
  _filterContext = context;
  _filterMask = layerMask;

  const hit = world.castRayAndGetNormal(
    _ray,
    maxDist,
    true,
    undefined,
    undefined,
    undefined,
    undefined,
    _filterPredicate
  );

  if (!hit) return null;

  const entity = context.colliderToEntity.get(hit.collider.handle);
  const toi = hit.timeOfImpact;
  const point = _ray.pointAt(toi);
  return {
    entity: entity ?? NULL_ENTITY,
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
  if (state.hasComponent(eid, WorldTransform)) {
    out.set(
      WorldTransform.posX[eid],
      WorldTransform.posY[eid],
      WorldTransform.posZ[eid]
    );
    return;
  }
  out.set(Transform.posX[eid], Transform.posY[eid], Transform.posZ[eid]);
}
