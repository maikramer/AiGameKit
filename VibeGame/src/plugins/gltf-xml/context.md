# GLTF-XML Plugin (context.md)
<!-- LLM:OVERVIEW -->
Declarative `<gltf-load>` XML tag for loading static GLB models into the scene. Handles async loading with ded in-flight tracking and position/rotation sync. For static props.
 No animation support.
 For animated player models, use `<player-gltf>`.
<!-- /LLM:OVERVIEW -->

## Layout

```
gltf-xml/
├── context.md     # This file
├── index.ts         # Exports
├── plugin.ts       # Plugin: GltfXmlPlugin (gltf-load recipe + GltfXmlLoadSystem + GltfPending component)
├── components.ts   # GltfPending component (loaded: ui8, 0/1)
├── systems.ts      # GltfXmlLoadSystem
└── recipes.ts      # gltfLoadRecipe (gltf-load recipe)
```

## Scope

- **In-scope**: Static GLB loading via declarative XML, position/rotation, scale, in-flight tracking
- **Out-of-scope**: Animated models (use player-gltf / player plugin), GLB generation, Draco decompression internals

## Entry Points
- **plugin.ts**: GltfXmlPlugin definition
- **systems.ts**: GltfXmlLoadSystem (setup group)
- **index.ts**: Re-exports

## Dependencies
- **Internal**: Core ECS (State, Transform), rendering (getScene, getRenderingContext)
- **External**: Three.js (GLTFLoader), `@loaders.gl/core` + `@loaders.gl/gltf` + `@loaders.gl/draco`, extras/gltf-bridge (loadGltfToScene)
<!-- LLM:REFERENCE -->
### Component
#### GltfPending
- loaded: ui8 (0 = pending, 1 = loaded/skip)

### System
#### GltfXmlLoadSystem
- Group: `setup`
- For each entity with `GltfPending` where `loaded === 0` and not in-flight:
- Loads GLB via `loadGltfToScene(state, url)`
- After load: applies `Transform` (position, scale, rotation/euler) to the loaded Three.js group
- Marks `loaded = 1`
- In-flight tracking prevents double-loading the same entity

### Recipe
- **gltf-load** — components: ['transform', 'gltfPending'], adapter: `url` stores URL in module-level map
<!-- /LLM:REFERENCE -->
<!-- LLM:EXAMPLES -->
## Examples
```xml
<gltf-load url="/assets/models/stone_pillar.glb" transform="pos: 10 2 -8; scale: 1.5 1.5" />
```
<!-- /LLM:EXAMPLES -->
