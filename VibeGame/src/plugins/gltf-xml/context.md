# GLTF-XML Plugin (context.md)

<!-- LLM:OVERVIEW -->

Declarative `<GLTFLoader>` and `<GLTFDynamic>` XML tags for loading GLB models. Static props use `GLTFLoader`; `GLTFDynamic` adds a **dynamic** Rapier body with a collider fitted to the model AABB after load (see `GltfDynamicPhysicsSystem` and `fitColliderFromAabb` in `GLTFDynamic-collider-fit.ts`). Shape is configurable: **box** (default), **sphere** (bounding sphere of the AABB), or **capsule** (Y-axis; `radius`/`height` are in world units — the physics pipeline does not scale these by `Transform` like box sizes). After the rigid body moves the ECS `Transform`, **`GltfSceneSyncSystem`** copies position/rotation/scale back to the loaded Three.js `Group` so the mesh stays aligned with physics (otherwise the collider moves but the GLB can appear stuck at the spawn pose).

**Animation:** these tags do **not** play clips by themselves. For a skinned LOD triple under a scripted entity (e.g. simple-rpg enemies): put `<GLTFLoader url=*_lod0 lod1-url lod2-url>` as a **child** of the `GameObject`, then attach `GltfAnimator` with `{ root: lodChild }` (clips from `loadGltfMasterTracked`). Player: `<PlayerGLTF>` (typically lod0 only). Root group: `getGltfRootGroup(state, eid)`.
<!-- /LLM:OVERVIEW -->

## Layout

```
gltf-xml/
├── context.md     # This file
├── index.ts         # Exports
├── plugin.ts       # Plugin: GltfXmlPlugin (recipes + sistemas)
├── components.ts   # GltfPending, GltfPhysicsPending
├── systems.ts      # GltfXmlLoadSystem (load GLB → cena)
├── auto-instance.ts # `instanced="true"` → InstancedMesh pool por URL
├── group-registry.ts # raiz Three.js por entidade (GLTFLoader / GLTFDynamic)
├── GLTFDynamic-system.ts # Body + Collider após AABB (Rapier)
├── GLTFDynamic-collider-fit.ts # AABB → Collider (box / sphere / capsule)
├── gltf-scene-sync.ts # ECS Transform / WorldTransform → mesh Three.js
└── recipes.ts      # gltfLoadRecipe, gltfDynamicRecipe
```

## Níveis de LOD fora do grafo

Um `gltf-lod-root` só mantém **o nível activo** parentado; os restantes ficam
num registo lateral (`extras/gltf-lod-parking.ts`). Motivo: `visible = false`
tira o nível do render mas não de `updateMatrixWorld`, que percorre e recompõe
todos os nós por frame — e cada nível de um prop rigado traz o seu esqueleto
(`simple-rpg`: 12,3k de 15,4k nós da cena escondidos, >11k deles ossos).
Destacar os inactivos levou a cena de **15 086 → 5 674 nós** e o grupo `render`
de **7,26 → 4,93 ms**.

Consequência para quem lê o grupo: `root.children` já **não** é a lista de
níveis. Usar `lodChildCount(root)` e `getLodChild(root, 0)` (BVH bake, fit de
colisor, mixer de animação). Quem precisa da matriz em **mundo** de um nível
parqueado tem de o reparentar primeiro — `bvh/static-meshes.ts` faz isso para o
bake e volta a destacá-lo.

O pipeline complementa isto pelo lado do asset: `text3d lod --rig-max-level`
(default 1) entrega LOD2 como mesh estático, para o esqueleto nem chegar ao
browser.

## Performance de meshes idênticos

- **Cache de master GLB** (`extras/gltf-bridge.ts`): cada URL é baixada/parseada
  **uma vez**; consumidores recebem `scene.clone(true)` — a hierarquia de nós é
  clonada, mas **geometrias e materiais são partilhados** (um upload de GPU por
  asset). Vale para `GLTFLoader`, `GLTFDynamic` e LODs. Caminhos animados
  (skinned) NÃO usam a cache. Atenção: mutar um material partilhado afeta
  todos os clones.
- **`<GLTFLoader instanced="true">`** (`auto-instance.ts`): todas as entidades
  com a mesma URL renderizam por **um `InstancedMesh2` por primitiva do GLB** —
  um draw call para o conjunto inteiro. Slots dinâmicos: destruir a entidade
  faz swap-remove do slot (props destrutíveis OK); `DistanceCull` →
  `setVisibilityAt`; matrizes só re-escrevem quando o Transform muda.
  LOD via `lod1-url` / `lod2-url` + `addLOD` na mesma pool. Entidades
  instanciadas não têm grupo próprio na cena (sem registo no group-registry →
  fora do BVH de meshes estáticos).
  **Armadilha:** `url === lod1-url` (mesma geometria partilhada) faz
  InstancedMesh2 aliasar o object LOD → instâncias somem ao aproximar.
  `normalizeInstancedLodUrls` descarta níveis duplicados; preferir
  `url=*_lod0` + `lod1-url=*_lod1` + `lod2-url=*_lod2`.
- **Spawn variation**: antes de `new InstancedMesh2` / `addLOD`, chamar
  `maybePatchInstanceVariationMaterial` + depois
  `initUniformsPerInstance(INSTANCE_VARIATION_UNIFORM_SCHEMA)`. Ver
  [`../spawn-variation/context.md`](../spawn-variation/context.md).
- **`spawnInstancedGltf(state, {...})`** (`spawn.ts`): a mesma pool a partir de
  código, para props cuja posição é **calculada** (cenário ao longo de uma
  spline, destroços de um script, muros de um layout gerado). A alternativa —
  clonar a cena por prop — é a armadilha clássica: 1500 barreiras ao longo de 5
  km viram 1500 draw calls e 1500 objetos para o three.js percorrer, ordenar e
  cullar por frame. Aceita `lod1Url`/`lod2Url`, `yaw` ou `quaternion`, `scale` e
  `cullDistance` (→ `DistanceCull`).
- **Custo por frame**: os slots estáticos são varridos em **shard rotativo**
  (`STATIC_SLOT_SCAN_INTERVAL`, máx. `MAX_STATIC_SCAN_PER_POOL` por pool/frame),
  não todos no mesmo frame — com ~30k plantas instanciadas a varredura completa
  era um pico de vários ms. Mudanças de `DistanceCull` chegam por **evento**
  (`rendering/cull-changes.ts`), por isso a varredura só existe para apanhar
  quem se mexeu sem estar marcado dinâmico. Slots dinâmicos (com `Parent` ou
  corpo não-fixo) continuam a ser verificados **todos os frames**.
- **Profiler**: `getInstancePoolStats(state)` → `{ poolCount, slotCount, pendingCount }`
  (painel Counters: `gltfInstances: …`).

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
- Loads GLB via `loadGltfToScene` / `loadGltfLodToSceneForEntity` / auto-instance pool
- LOD triples: **lod0 is boot-critical**; lod1/lod2 stream as background (do not block the loading `assets` gate). `GltfLodSystem` picks higher levels once attached.
- Instanced pools: lod0 counted critical via `loadGltfMasterTracked`; lod1/2 background.
- After load: applies `Transform` (position, scale, rotation/euler) to the loaded Three.js group
- Marks `loaded = 1`
- In-flight tracking prevents double-loading the same entity
- Boot readiness: `gltfAssetsReady(state)` — critical load count === 0 and every `GltfPending.loaded === 1`
- Once a URL's master parse **succeeds**, later requests for it never count toward the gate again (cache hit). Without this a per-frame caller — e.g. an entity script re-requesting a clip-less master to attach an animator — re-arms the gate every frame and the loading screen never fades. A _failed_ load does not mark the URL settled, so a genuine retry can still hold the gate.

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
