import { MAX_ENTITIES } from '../../core/ecs/constants';

/** Flag component placed on an entity to request navmesh generation.
 * The presence of an enabled NavMeshSurface entity triggers the init system. */
export const NavMeshSurface = {
  enabled: new Uint8Array(MAX_ENTITIES).fill(1),
  generated: new Uint8Array(MAX_ENTITIES),
} as const;

export const NavMeshWalkable = {
  enabled: new Uint8Array(MAX_ENTITIES).fill(1),
} as const;

/** Agent component. `agentIndex === -1` means no Crowd agent has been created yet. */
export const NavMeshAgent = {
  agentIndex: new Int32Array(MAX_ENTITIES).fill(-1),
  speed: new Float32Array(MAX_ENTITIES),
  radius: new Float32Array(MAX_ENTITIES).fill(0.4),
  height: new Float32Array(MAX_ENTITIES).fill(1.0),
  targetX: new Float32Array(MAX_ENTITIES),
  targetY: new Float32Array(MAX_ENTITIES),
  targetZ: new Float32Array(MAX_ENTITIES),
  hasTarget: new Uint8Array(MAX_ENTITIES),
  enabled: new Uint8Array(MAX_ENTITIES).fill(1),
} as const;
