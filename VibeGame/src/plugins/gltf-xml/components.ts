import { defineComponent, Types } from 'bitecs';

/** 0 = pendente; 1 = carregamento concluído (ou ignorado). */
export const GltfPending = defineComponent({
  loaded: Types.ui8,
});

/**
 * Após o GLB carregar, cria `Rigidbody` + `Collider` no AABB do modelo.
 * `ready`: 0 = aguardando; 1 = física aplicada.
 * `colliderShape`: valores de `ColliderShape` (box / sphere / capsule), campo no fim para não alterar layout dos restantes.
 */
export const GltfPhysicsPending = defineComponent({
  ready: Types.ui8,
  /** Metros somados ao tamanho do AABB por eixo (antes de dividir pelo scale do Transform). */
  colliderMargin: Types.f32,
  mass: Types.f32,
  friction: Types.f32,
  restitution: Types.f32,
  /** `ColliderShape` (0 = box, 1 = sphere, 2 = capsule). */
  colliderShape: Types.ui8,
  /** `BodyType` (0 = dynamic, 1 = fixed, 2 = kinematic-position, 3 = kinematic-velocity). */
  bodyType: Types.ui8,
});
