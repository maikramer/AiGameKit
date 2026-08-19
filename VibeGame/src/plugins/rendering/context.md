# Rendering Plugin

<!-- LLM:OVERVIEW -->

Lightweight Three.js rendering wrapper with meshes, lights, and cameras.
<!-- /LLM:OVERVIEW -->

## Layout

```
rendering/
├── context.md  # This file
├── index.ts  # Public exports
├── plugin.ts  # Plugin definition
├── components.ts  # Rendering components
├── recipes.ts  # Renderer recipe (transform + renderer)
├── systems.ts  # Rendering systems
├── operations.ts  # Mesh and shadow operations
├── matrix-freeze.ts  # Stop matrix updates for hidden subtrees (cull / LOD)
├── surface-detail.ts  # Procedural normal/roughness tiles for flat surfaces
└── utils.ts  # Canvas, context utilities, and constants
```

## O frustum de sombra segue a câmara (e assenta no texel)

`resolveShadowCenter` centra o frustum no alvo da `ThirdPersonCamera` e, **se
não houver nenhuma, na própria `MainCamera`** (deslocada `0.55 × raio` na
direção do olhar, para a caixa cobrir o que está no ecrã em vez da metade
atrás). Sem esse fallback qualquer jogo com câmara própria — a câmara de
perseguição do `simple-racer`, uma cinemática, um fly-cam — ficava com a caixa
de 64 m ancorada na origem do mundo: o shadow pass corria na mesma e o jogo
inteiro renderizava **sem uma única sombra do sol**.

`snapShadowCenterToTexels` quantiza esse centro à grelha de texels do shadow map
(no referencial da luz). Um centro que anda em frações de texel re-rasteriza
cada caster para texels ligeiramente diferentes todos os frames e as bordas das
sombras fervem — a 200 km/h é impossível não ver. Por isso o teste de "moveu-se"
usa 1e-4 e não 5 cm: com 4096 o texel tem 1,5 cm e um dead-band de 5 cm engolia
saltos inteiros.

**CSM (`csm: 1`) continua opt-in e não validado visualmente**: no `simple-racer`
as 3 cascatas montam-se, os materiais são patched (75) e as luzes entram na
cena, mas o resultado não tem sombra nenhuma. Até isso estar diagnosticado, o
caminho suportado é o mapa único acima (`pcss: 1` para a penumbra).

## Micro-detalhe de superfície (`surface-detail.ts`)

`applySurfaceDetail(material, kind, options)` sintetiza (uma vez, em
`DataTexture` 256²) um par normal + roughness a partir de fBm de value noise
com lattice cíclica — tileável, determinístico e sem assets para distribuir.
Kinds: `asphalt`, `gravel`, `dirt`, `concrete`, `metal`.

Porque existe: um `MeshStandardMaterial` com cor lisa e roughness escalar tem
zero variação sub-métrica, portanto uma estrada devolve exatamente o mesmo
specular em centenas de m² — é isso que lê como plástico, por melhor que seja a
luz. O mapa de roughness é escrito como **multiplicador** em `[1 - variância,
1]` e o escalar do material é pré-dividido pela média, para o valor pedido
continuar a ser a média da superfície.

O `repeat` vive na Texture e não no slot do material, por isso cada par
(kind, repeatX, repeatY) tem o seu clone: dois materiais a partilhar a mesma
Texture não podem pedir tilings diferentes — o último a atribuir retilava todos.
Ribbons (estrada) precisam de eixos separados: o U atravessa a largura da pista
enquanto o V conta metros de circuito.

## Hidden subtrees cost frames

`visible = false` keeps the renderer out of a subtree, but
`Object3D.updateMatrixWorld` ignores visibility — every node under it is still
recomposed each frame. In a dressed world the hidden part of the graph is the
*majority* (simple-rpg: ~12.3k of 15.4k nodes, most of them bones of culled
props and of inactive LOD children). `setSubtreeMatrixFrozen` clears
`matrixAutoUpdate` across such a subtree and restores it — plus one forced
world-matrix refresh — on the way back. Callers: `DistanceCullSystem` (on cull
flips) and `GltfLodSystem` (inactive LOD children, and roots whose GLB finished
loading after the entity was already culled).

## Scope

- **In-scope**: Three.js rendering, mesh management, lighting, camera sync, canvas sizing
- **Out-of-scope**: Post-processing effects (handled by postprocessing plugin), Physics visualization, UI overlays

## Canvas Sizing

Renderer and camera use `canvas.clientWidth/clientHeight` for sizing and aspect ratio, respecting CSS dimensions. Multiple canvases per page require separate State instances (one State per canvas).

**Shader warmup / postprocessing:** EffectComposer can start with 0×0 depth
(Firefox: `DEPTH_ATTACHMENT…`). `syncComposerSize` re-syncs after construct,
on window resize, and before each composer frame (skips draw until sized).
Renderer setup calls an initial resize so the first frame is not 0×0.
Warmup **must not** block the loading `shaders` gate on a single huge frame:
compile + one raw `renderer.render` (no composer), latch immediately, then
finish yaw×pitch orbit across later frames. Waiting on camera/drawing-buffer
logs and force-latches after a frame budget. Directional boot light starts with
`castShadow=false`; `shadowMapSize` clamped to ≥1 when shadows are enabled.

## Performance

- **Dynamic instance pooling**: Starts at 1000 instances per shape, automatically doubles when full
- **Performance warning**: Console warning at 10,000 total instances
- **Hard limit**: 50,000 total instances (throws error)
- **Roblox-like scaling**: Graceful growth with developer-friendly warnings

### Slots de PointLight (12)

`MAX_POINT_LIGHTS = 12` é o tecto de luzes pontuais simultâneas. Os slots são
atribuídos **às mais próximas da câmera** (`pickNearestLightSlots`), não por
ordem de criação:

- Antes era primeiro-a-chegar: num mundo aberto, uma dúzia de lanternas junto à
  origem ficava com todos os slots no boot e **todas** as tochas/braseiros que o
  jogador visitasse depois nasciam apagadas — com um aviso por entidade **por
  frame** (30k linhas em dois minutos no simple-rpg).
- Quem tem slot leva 25% de vantagem de distância (histerese) para a luz não
  piscar quando o jogador anda na fronteira entre dois grupos.
- Ao perder o slot a luz volta a um pool com `intensity = 0` **dentro da cena**:
  tirá-la mudaria a contagem de luzes, que está compilada em todos os programas
  do material cache e obrigaria a recompilar a cena inteira. Pela mesma razão a
  destruição de uma entidade devolve a luz ao pool em vez de a dispor.
- O aviso do tecto é emitido **uma vez por entidade**.

## Entry Points

- **plugin.ts**: RenderingPlugin bundles all components, systems, and recipes
- **systems.ts**: Rendering systems executed each frame
- **index.ts**: Public API exports

## Dependencies

- **Internal**: Transforms plugin (WorldTransform component)
- **External**: Three.js

<!-- LLM:REFERENCE -->

### Components

#### Renderer

- shape: ui8 - 0=box, 1=sphere
- sizeX, sizeY, sizeZ: f32 (1)
- color: ui32 (0xffffff)
- visible: ui8 (1)
- unlit: ui8 (0) - Use unlit material (ignores lighting)

#### RenderContext

- clearColor: ui32 (0x000000)
- hasCanvas: ui8

#### MainCamera

- projection: ui8 (0) - 0=perspective, 1=orthographic
- fov: f32 (75) - Field of view in degrees (perspective only)
- orthoSize: f32 (10) - Vertical size in world units (orthographic only)

#### AmbientLight

- skyColor: ui32 (0x87ceeb)
- groundColor: ui32 (0x4a4a4a)
- intensity: f32 (0.6)

#### DirectionalLight

- color: ui32 (0xffffff)
- intensity: f32 (1)
- castShadow: ui8 (1)
- shadowMapSize: ui32 (4096)
- directionX: f32 (-1)
- directionY: f32 (2)
- directionZ: f32 (-1)
- distance: f32 (30)

### Systems

#### MeshInstanceSystem

- Group: draw
- Synchronizes transforms with Three.js meshes

#### LightSyncSystem

- Group: draw
- Updates Three.js lights

#### CameraSyncSystem

- Group: draw
- Synchronizes camera position and rotation from WorldTransform

#### WebGLRenderSystem

- Group: draw (last)
- Renders scene directly via WebGLRenderer (or through EffectComposer if postprocessing plugin is active)

### Functions

#### setCanvasElement(entity, canvas): void

Associates canvas with RenderContext
<!-- /LLM:REFERENCE -->

<!-- LLM:EXAMPLES -->

## Examples

### Basic Rendering Setup

```xml
<!-- Declarative scene with lighting and rendered objects -->
<Scene canvas="#game-canvas" sky="#87ceeb">
  <!-- Lighting (auto-created if omitted) -->
  <GameObject ambient-light directional-light></GameObject>

  <!-- Rendered box using <MeshRenderer> recipe -->
  <MeshRenderer shape="box" color="#ff0000" size-x="2" pos="0 1 0"></MeshRenderer>

  <!-- Rendered sphere -->
  <MeshRenderer shape="sphere" color="#00ff00" pos="3 1 0"></MeshRenderer>
</Scene>
```

### Custom Lighting

```xml
<!-- Combined lighting entity with custom properties -->
<GameObject   ambient-light="sky-color: 0xffd4a3; ground-color: 0x808080; intensity: 0.4"
  directional-light="color: 0xffffff; intensity: 1.5; direction-x: -1; direction-y: 3; direction-z: -0.5; cast-shadow: 1; shadow-map-size: 2048"
></GameObject>

<!-- Or separate entities for independent control -->
<GameObject ambient-light="sky-color: 0xffd4a3; intensity: 0.4"></GameObject>
<GameObject directional-light="intensity: 1.5; direction-y: 3"></GameObject>
```

### Imperative Usage

```typescript
import * as GAME from 'vibegame';

// Create rendered entity programmatically
const entity = state.createEntity();

// Add transform for positioning
state.addComponent(entity, GAME.Transform, {
  posX: 0, posY: 5, posZ: 0
});

// Add renderer component
state.addComponent(entity, GAME.Renderer, {
  shape: 1,        // sphere
  sizeX: 2,
  sizeY: 2,
  sizeZ: 2,
  color: 0xff00ff,
  visible: 1
});

// Set canvas for rendering context
const contextQuery = GAME.defineQuery([GAME.RenderContext]);
const contextEntity = contextQuery(state.world)[0];
const canvas = document.getElementById('game-canvas');
GAME.setCanvasElement(contextEntity, canvas);
```

### Shape Types

```typescript
import * as GAME from 'vibegame';

// Available shape enums
const shapes = {
  box: 0,
  sphere: 1
};

// Use in XML
<GameObject renderer="shape: sphere"></GameObject>

// Or with enum names
<GameObject renderer="shape: 1"></GameObject>
```

### Visibility Control

```typescript
import * as GAME from 'vibegame';

// Hide/show entities
GAME.Renderer.visible[entity] = 0; // Hide
GAME.Renderer.visible[entity] = 1; // Show

// In XML
<GameObject renderer="visible: 0"></GameObject>  <!-- Initially hidden -->
```

### Unlit Rendering

```xml
<!-- Emissive/unlit objects (not affected by lighting) -->
<GameObject renderer="shape: sphere; color: 0xffff00; unlit: 1"></GameObject>
```

### Orthographic Camera

```xml
<!-- Orthographic projection for 2D-style rendering -->
<camera main-camera="projection: orthographic; ortho-size: 20"></camera>

<!-- Perspective (default) with custom FOV -->
<camera main-camera="projection: perspective; fov: 60"></camera>
```

<!-- /LLM:EXAMPLES -->
