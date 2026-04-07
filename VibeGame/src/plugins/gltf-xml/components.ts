import { defineComponent, Types } from 'bitecs';

/** 0 = pendente; 1 = carregamento concluído (ou ignorado). */
export const GltfPending = defineComponent({
  loaded: Types.ui8,
});

/**
 * Após o GLB carregar, cria `Body` + `Collider` com caixa no AABB do modelo.
 * `ready`: 0 = aguardando; 1 = física aplicada.
 */
export const GltfPhysicsPending = defineComponent({
  ready: Types.ui8,
  /** Metros somados ao tamanho do AABB por eixo (antes de dividir pelo scale do Transform). */
  colliderMargin: Types.f32,
  mass: Types.f32,
  friction: Types.f32,
  restitution: Types.f32,
});
