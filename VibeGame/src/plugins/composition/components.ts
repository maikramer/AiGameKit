import { defineComponent, U8 } from '../../core/ecs/component-storage';

// Two-phase build: meshes realize in the `setup` group, colliders in `fixed`
// after the Rapier body exists. Each flag flips to 1 once realized.
export const CompositionPending = defineComponent({
  meshBuilt: U8,
  colliderBuilt: U8,
});
