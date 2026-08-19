import {
  defineComponent,
  F32,
  U32,
  U8,
} from '../../core/ecs/component-storage';

/** Origem do raio: posição vem de WorldTransform; direção em espaço mundo. */
export const RaycastSource = defineComponent({
  dirX: F32,
  dirY: F32,
  dirZ: F32,
  maxDist: F32,
  layerMask: U32,
  mode: U8,
});

/** Resultado preenchido por RaycastSystem. */
export const RaycastHit = defineComponent({
  hitValid: U8,
  hitEntity: U32,
  hitDist: F32,
  hitNormalX: F32,
  hitNormalY: F32,
  hitNormalZ: F32,
  hitPointX: F32,
  hitPointY: F32,
  hitPointZ: F32,
});
