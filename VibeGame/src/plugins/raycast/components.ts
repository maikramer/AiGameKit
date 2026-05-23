import { MAX_ENTITIES } from '../../core/ecs/constants';

/** Origem do raio: posição vem de WorldTransform; direção em espaço mundo. */
export const RaycastSource = {
  dirX: new Float32Array(MAX_ENTITIES),
  dirY: new Float32Array(MAX_ENTITIES),
  dirZ: new Float32Array(MAX_ENTITIES),
  maxDist: new Float32Array(MAX_ENTITIES),
  layerMask: new Uint32Array(MAX_ENTITIES),
  mode: new Uint8Array(MAX_ENTITIES),
} as const;

/** Resultado preenchido por RaycastSystem. */
export const RaycastHit = {
  hitValid: new Uint8Array(MAX_ENTITIES),
  hitEntity: new Uint32Array(MAX_ENTITIES),
  hitDist: new Float32Array(MAX_ENTITIES),
  hitNormalX: new Float32Array(MAX_ENTITIES),
  hitNormalY: new Float32Array(MAX_ENTITIES),
  hitNormalZ: new Float32Array(MAX_ENTITIES),
  hitPointX: new Float32Array(MAX_ENTITIES),
  hitPointY: new Float32Array(MAX_ENTITIES),
  hitPointZ: new Float32Array(MAX_ENTITIES),
} as const;
