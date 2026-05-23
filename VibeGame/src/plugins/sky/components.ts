import { MAX_ENTITIES } from '../../core/ecs/constants';

/** Configuração de sky environment por entidade. */
export const Skybox = {
  urlIndex: new Uint32Array(MAX_ENTITIES),
  rotationDeg: new Float32Array(MAX_ENTITIES),
  setBackground: new Uint8Array(MAX_ENTITIES),
  loaded: new Uint8Array(MAX_ENTITIES),
} as const;
