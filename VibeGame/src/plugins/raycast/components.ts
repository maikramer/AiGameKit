import { defineComponent, Types } from 'bitecs';

/** Origem do raio: posição vem de WorldTransform; direção em espaço mundo. */
export const RaycastSource = defineComponent({
  dirX: Types.f32,
  dirY: Types.f32,
  dirZ: Types.f32,
  maxDist: Types.f32,
  layerMask: Types.ui32,
  /** 0 = só Rapier, 1 = tentar malhas Three (BVH) quando registadas */
  mode: Types.ui8,
});

/** Resultado preenchido por RaycastSystem. */
export const RaycastResult = defineComponent({
  hitValid: Types.ui8,
  hitEntity: Types.eid,
  hitDist: Types.f32,
  hitNormalX: Types.f32,
  hitNormalY: Types.f32,
  hitNormalZ: Types.f32,
  hitPointX: Types.f32,
  hitPointY: Types.f32,
  hitPointZ: Types.f32,
});
