import { MAX_ENTITIES } from '../../core/ecs/constants';

/** 0 = pendente; 1 = carregamento concluído (ou ignorado). */
export const GltfPending = {
  loaded: new Uint8Array(MAX_ENTITIES),
} as const;

/**
 * Após o GLB carregar, cria `Rigidbody` + `Collider` no AABB do modelo.
 * `ready`: 0 = aguardando; 1 = física aplicada.
 * `colliderShape`: valores de `ColliderShape` (box / sphere / capsule), campo no fim para não alterar layout dos restantes.
 */
/**
 * Três variantes GLB (lod0/lod1/lod2) sob um único `Group`; visibilidade por distância à câmara.
 * Requer `lod-urls` no `<gltf-load>` e carregamento triplo no sistema de load.
 */
export const GltfLod = {
  thresholdNear: new Float32Array(MAX_ENTITIES),
  thresholdMid: new Float32Array(MAX_ENTITIES),
  activeLevel: new Uint8Array(MAX_ENTITIES),
} as const;

export const GltfPhysicsPending = {
  ready: new Uint8Array(MAX_ENTITIES),
  colliderMargin: new Float32Array(MAX_ENTITIES),
  mass: new Float32Array(MAX_ENTITIES),
  friction: new Float32Array(MAX_ENTITIES),
  restitution: new Float32Array(MAX_ENTITIES),
  colliderShape: new Uint8Array(MAX_ENTITIES),
  bodyType: new Uint8Array(MAX_ENTITIES),
} as const;
