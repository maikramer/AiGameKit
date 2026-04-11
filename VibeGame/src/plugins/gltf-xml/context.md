# GLTF-XML Plugin (context.md)
<!-- LLM:OVERVIEW -->
Declarative `<GLTFLoader>` and `<GLTFDynamic>` XML tags for loading GLB models. Static props use `GLTFLoader`; `GLTFDynamic` adds a **dynamic** Rapier body with a collider fitted to the model AABB after load (see `GltfDynamicPhysicsSystem` and `fitColliderFromAabb` in `GLTFDynamic-collider-fit.ts`). Shape is configurable: **box** (default), **sphere** (bounding sphere of the AABB), or **capsule** (Y-axis; `radius`/`height` are in world units — the physics pipeline does not scale these by `Transform` like box sizes). After the rigid body moves the ECS `Transform`, **`GltfSceneSyncSystem`** copies position/rotation/scale back to the loaded Three.js `Group` so the mesh stays aligned with physics (otherwise the collider moves but the GLB can appear stuck at the spawn pose). No GLB animation in these tags. For animated player models, use `<PlayerGLTF>`.
<!-- /LLM:OVERVIEW -->

## Layout

```
gltf-xml/
├── context.md     # This file
├── index.ts         # Exports
├── plugin.ts       # Plugin: GltfXmlPlugin (recipes + sistemas)
├── components.ts   # GltfPending, GltfPhysicsPending
├── systems.ts      # GltfXmlLoadSystem (load GLB → cena)
├── group-registry.ts # raiz Three.js por entidade (GLTFLoader / GLTFDynamic)
├── GLTFDynamic-system.ts # Body + Collider após AABB (Rapier)
├── GLTFDynamic-collider-fit.ts # AABB → Collider (box / sphere / capsule)
├── gltf-scene-sync.ts # ECS Transform / WorldTransform → mesh Three.js
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
- **GLTFLoader** — components: `transform`, `gltfPending`; adapter `url` guarda URL no mapa do módulo.
- **GLTFDynamic** — components: `transform`, `gltfPending`, `gltfPhysicsPending`; mesmo fluxo de `url` que `GLTFLoader`. Defaults `gltfPhysicsPending`: `collider-margin`, `collider-shape` (`box` \| `sphere` \| `capsule`), `mass`, `friction`, `restitution`. Após load, `GltfDynamicPhysicsSystem` cria `Body` (Dynamic) + `Collider` com forma escolhida e tamanho a partir do AABB (+ margem). Para **box** e **sphere**, as dimensões do collider compensam o `scale` do `Transform`; **capsule** usa `radius` / `height` em unidades mundo (ver nota no overview).

### Systems (ordem no plugin)

1. **GltfXmlLoadSystem** (`setup`) — carrega o GLB e aplica `Transform` inicial ao grupo Three.js.
2. **GltfDynamicPhysicsSystem** (`simulation`) — quando o mesh está carregado e há AABB, cria corpo e colisor Rapier.
3. **GltfSceneSyncSystem** (`simulation`, após `TransformHierarchySystem`) — para cada entidade com GLB carregado, copia `Transform` ou `WorldTransform` para o `Group` raiz registado. Necessário para `GLTFDynamic`: a física atualiza o ECS, e sem este passo o modelo 3D não acompanha o corpo.

<!-- /LLM:REFERENCE -->
<!-- LLM:EXAMPLES -->
## Examples
```xml
<GLTFLoader url="/assets/models/stone_pillar.glb" transform="pos: 10 2 -8; scale: 1.5 1.5" />
<GLTFDynamic
  url="/assets/models/wooden_crate.glb"
  transform="pos: 2 0.5 2; scale: 1 1 1"
  mass="2"
  friction="0.6"
  collider-margin="0.03"
  collider-shape="box"
></GLTFDynamic>
```
Atributos opcionais `mass`, `friction`, `restitution`, `collider-margin`, `collider-shape` (`box` \| `sphere` \| `capsule`) aplicam-se ao componente `gltfPhysicsPending` (via recipe).

<!-- /LLM:EXAMPLES -->
