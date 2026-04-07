# GLTF-XML Plugin (context.md)
<!-- LLM:OVERVIEW -->
Declarative `<gltf-load>` and `<gltf-dynamic>` XML tags for loading GLB models. Static props use `gltf-load`; `gltf-dynamic` adds a **dynamic** Rapier body with a **box collider** fitted to the model AABB after load (see `GltfDynamicPhysicsSystem`). No GLB animation in these tags. For animated player models, use `<player-gltf>`.
<!-- /LLM:OVERVIEW -->

## Layout

```
gltf-xml/
├── context.md     # This file
├── index.ts         # Exports
├── plugin.ts       # Plugin: GltfXmlPlugin (gltf-load recipe + GltfXmlLoadSystem + GltfPending component)
├── components.ts   # GltfPending component (loaded: ui8, 0/1)
├── systems.ts      # GltfXmlLoadSystem
├── group-registry.ts # raiz Three.js por entidade (gltf-dynamic)
├── gltf-dynamic-system.ts # Body/Collider após AABB
└── recipes.ts      # gltfLoadRecipe, gltfDynamicRecipe
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
- **gltf-dynamic** — components: ['transform', 'gltfPending', 'gltfPhysicsPending']; mesmo `url`; defaults `gltfPhysicsPending`: `collider-margin`, `mass`, `friction`, `restitution`; após load, cria `Body` (Dynamic) + `Collider` (box) com tamanho do AABB (+ margem), compensando o `scale` do Transform no componente collider.

<!-- /LLM:REFERENCE -->
<!-- LLM:EXAMPLES -->
## Examples
```xml
<gltf-load url="/assets/models/stone_pillar.glb" transform="pos: 10 2 -8; scale: 1.5 1.5" />
<gltf-dynamic
  url="/assets/models/wooden_crate.glb"
  transform="pos: 2 0.5 2; scale: 1 1 1"
  mass="2"
  friction="0.6"
  collider-margin="0.03"
></gltf-dynamic>
```
Atributos opcionais `mass`, `friction`, `restitution`, `collider-margin` aplicam-se ao componente `gltfPhysicsPending` (via recipe).

<!-- /LLM:EXAMPLES -->
