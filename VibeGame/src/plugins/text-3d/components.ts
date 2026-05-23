import { MAX_ENTITIES } from '../../core/ecs/constants';

/**
 * Entidade que carrega um modelo GLTF/GLB gerado pelo Text3D (pipeline Hunyuan).
 * O modelo é tratado como uma mesh estática — sem colisão por padrão.
 */
export const TextMesh = {
  pending: new Uint8Array(MAX_ENTITIES),
  scale: new Float32Array(MAX_ENTITIES),
  tint: new Uint32Array(MAX_ENTITIES),
} as const;

export const Text3dContext = {
  _loaded: new Uint8Array(MAX_ENTITIES),
} as const;
