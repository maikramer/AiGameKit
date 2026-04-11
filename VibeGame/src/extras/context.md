# GLTF Loader (context.md)

<!-- LLM:OVERVIEW -->
Two-stage GLB loading pipeline: `loaders.gl` decompress + Three.js `GLTFLoader.parseAsync`).
 Static GLB models become Three.js scenes with animation clips.
 Wraps animated models in `GltfAnimator` for runtime clip playback.
 `PlayerGltfSetupSystem` handles movement input and walk/runidle animation. `PlayerGltfAnimStateSystem` handles sync per frame. For static props, use `<GLTFLoader>` in XML. For static environment objects. Use `<PlayerGLTF>` in XML for animated player character models. Can also be loaded programmatically via the APIs (`loadGltfToScene`, `loadGltfAnimated`, `loadGltfToSceneWithAnimator`). `GltfAnimator` class for runtime animation control. `animatorRegistry`/`registerAnimator` for ECS integration with `GltfAnimPlugin`. `GltfAnimationState` component syncs animator root to `WorldTransform`.
<!-- /LLM:OVERVIEW -->

## Layout



```
extras/
├── context.md
 # This file
├── gltf-bridge.ts    # Core loading functions (loaders.gl → Three.js → GLTFLoader)
├── gltf-animator.ts   # Runtime animation controller (GltfAnimator (AnimationMixer wrapper)
├── sky-env.ts          # Equirect sky environment map
gltf-xml/
│   ├── context.ts      # Runtime state storage (WeakMap)
│   ├── index.ts         # Exports
│   ├── plugin.ts      # Plugin: GltfXmlPlugin
GLTFLoader recipe + GltfXmlLoadSystem + GltfPending component)
│   ├── components.ts  # GltfPending component
│   ├── systems.ts     # GltfXmlLoadSystem
│   └── recipes.ts   # gltfLoadRecipe (GLTFLoader recipe)
gltf-anim/
│   ├── context.md     # THIS FILE
│   ├── index.ts         # Exports
│   ├── plugin.ts      # Plugin: GltfAnimPlugin (gltf-anim recipe + GltfAnimationUpdateSystem + GltfAnimationState component)
│   ├── components.ts  # GltfAnimationState component
│   └── systems.ts     # GltfAnimationUpdateSystem, animatorRegistry, registerAnimator
```

<!-- /LLM:REFERENCE -->
### Functions

#### loadGltfToScene(state, url): Promise<Group>
Loads GLB, adds scene to render graph, returns only the `Group` (no animation access). For static props/environment objects.

 Use in `index.html` via the `<GLTFLoader>` tag.

 ```typescript
const group = await loadGltfToScene(state, url);
 ```

#### loadGltfAnimated(state, url): Promise<GLTF>
Same as `loadGltfToScene` but returns full `GLTF` object with `scene + animations`. For animated models that need `GltfAnimator`. for runtime clip playback. Use `GltfAnimator` class.

 ```typescript
const gltf = await loadGltfAnimated(state, url);
const animator = new GltfAnimator(gltf, { crossfadeDuration: 0.25 });
animator.play('Animator3D_BreatheIdle');
 ```

#### loadGltfToSceneWithAnimator(state, url, options?): Promise<GltfLoadResult>
Convenience wrapper. Loads GLB, adds to scene, and if animations exist creates `GltfAnimator` and registers it in ECS. Returns `{ group, GL animator }`. `animator` is `null` if no animations.

 Options: `crossfadeDuration` overrides default ( `GltfAnimator` constructor crossfade (default 0.25).

 All three require a `State` object with valid Three.js scene (rejects if headless/rendering not ready).

 ```typescript
const { group, animator } = await loadGltfToSceneWithAnimator(state, '/assets/models/hero.glb');
if (animator) {
  animator.play('walk');
}
 ```

### Classes
#### GltfAnimator
Runtime animation controller wrapping Three.js `AnimationMixer`.

 - Constructor takes a `GLTF` object and `crossfadeDuration` (default 0.25)
- Properties: `mixer`, `clips`, `clipNames`, `activeClipName`, `root`
- `play(clipName)` — crossfade to current clip, Skips if already playing. `update(deltaTime)` — ticks the mixer. `dispose()` — stops all actions, ununcaches root.

 ```typescript
const animator = new GltfAnimator(gltf);
console.log(animator.clipNames); // ['Animator3D_BreatheIdle', 'Animator3D_Walk']
animator.play('Animator3D_BreatheIdle');
 ```

### Functions
#### registerAnimator(animator): number
Registers a `GltfAnimator` in the global `animatorRegistry` map. Returns the numeric index.

 ```typescript
import { registerAnimator } from 'vibegame';
const animator = new GltfAnimator(gltf);
const idx = registerAnimator(animator);
 ```

### Recipes
- **GLTFLoader** — components: ['transform', 'gltfPending']. Static GLB entities in the scene via `<GLTFLoader>` XML tag.
- **player-gltf** — components: [...playerRecipe.components, 'playerGltfConfig']. Full player gameplay stack with GLB-driven visuals and animation. `PlayerGLTF` XML tag. The `model-url` adapter stores the model URL in a module-level map.

 `PlayerGltfSetupSystem` loads GLB via `loadGltfAnimated`, creates `GltfAnimator`, plays default idle clip. `PlayerGltfAnimStateSystem` handles idle/walk/run animation state. `PlayerGltfEnsureHasAnimatorSystem` prevents procedural box character from spawning by adding `HasAnimator` component. `GltfAnimationUpdateSystem` ticks the `GltfAnimator` via `animatorRegistry` and syncs `animator.root` position/rotation to `WorldTransform`. Expected clip names: `Animator3D_BreatheIdle`, `Animator3D_Walk`, `Animator3D_Run` (requires Shift key modifier).

 Default `BreatheIdle`. When movement keys + Shift → `Run`. Otherwise `Walk`. Fallback chain walk → idle if clip not found. For run → walk. For walk → idle if walk not found).

 Also sync hero position from `WorldTransform` via `PlayerGltfAnimStateSystem`.
<!-- /LLM:REFERENCE -->

<!-- LLM:EXAMPLES -->
## Examples
### Declarative Static Prop
```xml
<GLTFLoader url="/assets/models/stone_pillar.glb" transform="pos: 10 2 -8; scale: 1.5 1.5" />
```

### Declarative Animated Player
```xml
<PlayerGLTF model-url="/assets/models/hero.glb" pos="0 60 0"></PlayerGLTF>
```

### Programmatic Loading
```typescript
import * as GAME from 'vibegame';
import { loadGltfAnimated } from 'vibegame';
import { GltfAnimator } from 'vibegame';

const gltf = await loadGltfAnimated(state, '/assets/models/hero.glb');
const animator = new GltfAnimator(gltf);
animator.play('Animator3D_BreatheIdle');
```
