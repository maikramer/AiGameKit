import { MAX_ENTITIES } from '../../core/ecs/constants';

// Two-phase build: meshes realize in the `setup` group, colliders in `fixed`
// after the Rapier body exists. Each flag flips to 1 once realized.
export const CompositionPending = {
  meshBuilt: new Uint8Array(MAX_ENTITIES),
  colliderBuilt: new Uint8Array(MAX_ENTITIES),
} as const;
