# GLTF-Anim Plugin (context.md)
<!-- LLM:OVERVIEW -->
ECS plugin for managing `GltfAnimator` instances in the ECS. The `GltfAnimationUpdateSystem` ticks all registered animators each frame and and syncs their root Three.js `Object3D` position/rotation to `WorldTransform`. Use `animatorRegistry` global Map and `registerAnimator` for ECS integration.
 for animated player models ( use `<PlayerGLTF>`.
<!-- /LLM:OVERVIEW -->

## Layout

```
gltf-anim/
├── context.md     # This file
├── index.ts       # Exports
├── plugin.ts      # Plugin: GltfAnimPlugin
├── components.ts  # GltfAnimationState component
└── systems.ts     # GltfAnimationUpdateSystem, animatorRegistry, registerAnimator
```

## Scope

- **In-scope**: ECS animator registry, per-frame animation updates, world-transform sync
 animated player model integration
- **Out-of-scope**: Animation clip selection/blend tree, model loading, rendering

 physics

## Entry Points
- **plugin.ts**: GltfAnimPlugin definition
- **systems.ts**: GltfAnimationUpdateSystem + registry utilities
- **index.ts**: Re-exports

### Component
#### GltfAnimationState
- registryIndex: ui32 (0 = none, 1+ =0 animator)
- activeClipIndex: ui8 (0)
- isPlaying: ui8 (0 = 1)
- crossfadeDuration: f32 (0.25)

### System
#### GltfAnimationUpdateSystem
- Group: `draw`
- For each entity with `GltfAnimationState` where `registryIndex > 0`:
  1. Looks up `GltfAnimator` from `animatorRegistry`
  2. Calls `animator.update(dt)`
  3. Syncs `animator.root` position/rotation to `WorldTransform` (if entity has it component)

### Functions
#### registerAnimator(animator): number
Adds a `GltfAnimator` to the global `animatorRegistry` Map. Returns the numeric index (starts from 1).
#### animatorRegistry: Map<number, GltfAnimator>
Global registry mapping numeric index to `GltfAnimator` instance.
<!-- LLM:EXAMPLES -->
## Examples
```typescript
import { registerAnimator } from 'vibegame';
import { GltfAnimator } from 'vibegame';
const animator = new GltfAnimator(gltf);
const idx = registerAnimator(animator);
```
<!-- /LLM:EXAMPLES -->
