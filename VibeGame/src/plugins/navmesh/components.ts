import { MAX_ENTITIES } from '../../core/ecs/constants';

/** Marca zona de navegação (recipe `nav-mesh`). */
export const NavMeshSurface = {
  loaded: new Uint8Array(MAX_ENTITIES),
  buildFromScene: new Uint8Array(MAX_ENTITIES),
} as const;

/** Agente que segue caminho no navmesh. */
export const NavMeshAgent = {
  targetX: new Float32Array(MAX_ENTITIES),
  targetY: new Float32Array(MAX_ENTITIES),
  targetZ: new Float32Array(MAX_ENTITIES),
  speed: new Float32Array(MAX_ENTITIES),
  tolerance: new Float32Array(MAX_ENTITIES),
  status: new Uint8Array(MAX_ENTITIES),
} as const;
