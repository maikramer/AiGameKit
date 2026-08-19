import { defineComponent, F32, U8 } from '../../core/ecs/component-storage';

/** 0 = pendente; 1 = carregamento concluído (ou ignorado). `retries` conta tentativas após falha. */
export const GltfPending = defineComponent({
  loaded: U8,
  retries: U8,
});

/**
 * Após o GLB carregar, cria `Rigidbody` + `Collider` no AABB do modelo.
 * `ready`: 0 = aguardando; 1 = física aplicada.
 * `colliderShape`: valores de `ColliderShape` (box / sphere / capsule), campo no fim para não alterar layout dos restantes.
 */
export const GltfPhysicsPending = defineComponent({
  ready: U8,
  colliderMargin: F32,
  mass: F32,
  friction: F32,
  restitution: F32,
  colliderShape: U8,
  bodyType: U8,
});

/**
 * Três variantes GLB (lod0/lod1/lod2) sob um único `Group`; visibilidade por distância à câmara.
 * Requer `lod-urls` no `<GLTFLoader>` e carregamento triplo no sistema de load.
 */
export const GltfLod = defineComponent({
  thresholdNear: F32,
  thresholdMid: F32,
  activeLevel: U8,
  /** 1 once LOD child visibility has been applied correctly (skip rescan). */
  settled: U8,
});
