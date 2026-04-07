# Terrain Plugin

<!-- LLM:OVERVIEW -->
LOD terrain rendering with heightmap displacement, Sobel normals, chunk-based Rapier heightfield colliders, and automatic LOD frustum culling. Uses `@interverse/three-terrain-lod` for mesh management with a custom WebGL material provider.
<!-- /LLM:OVERVIEW -->

## Layout

```
terrain/
├── context.md            # This file
├── index.ts              # Public exports
├── plugin.ts             # Plugin definition + config defaults/adapters
├── components.ts         # Terrain ECS component
├── systems.ts            # Bootstrap, render, physics systems
├── recipes.ts            # <terrain> recipe
├── utils.ts              # Context, heightmap/texture URL setters, height resampling
└── webgl-material.ts     # WebGL terrain material with displacement + normals
```

## Scope

- **In-scope**: Heightmap-based terrain, LOD rendering, heightfield physics colliders, chunk streaming, heightmap/texture URL configuration
- **Out-of-scope**: Procedural generation, erosion, vegetation, water rendering

## Entry Points

- **plugin.ts**: TerrainPlugin with recipes, systems, components, config defaults and adapters (`heightmap`, `texture` URL attributes)
- **systems.ts**: Three systems — bootstrap (fixed), render (draw), physics (simulation)
- **index.ts**: Public API — `Terrain`, `TerrainPlugin`, `terrainRecipe`, `getTerrainContext`, `TerrainEntityData`

## Dependencies

- **Internal**: Core ECS, transforms (WorldTransform), rendering (MainCamera, Scene, Renderer), physics (RAPIER, PhysicsWorld)
- **External**: `@interverse/three-terrain-lod` (TerrainLOD mesh), Three.js, Rapier WASM

<!-- LLM:REFERENCE -->
### Components

#### Terrain
- worldSize: f32 (256) — world-space extent of the terrain (X × Z)
- maxHeight: f32 (50) — maximum height displacement from heightmap
- levels: ui8 (6) — LOD levels for chunk subdivision
- resolution: ui8 (64) — vertices per chunk side
- lodDistanceRatio: f32 (2.0) — distance multiplier between LOD levels
- wireframe: ui8 (0) — wireframe rendering mode

### Systems

#### TerrainBootstrapSystem
- Group: `fixed` (after PhysicsWorldSystem)
- Creates TerrainLOD instance with WebGLTerrainMaterialProvider
- Loads heightmap/texture from URLs set via adapters
- Disposes terrain for removed entities

#### TerrainRenderSystem
- Group: `draw` (after CameraSyncSystem)
- Updates LOD frustum culling and syncs WorldTransform position to TerrainLOD

#### TerrainPhysicsSystem
- Group: `simulation` (after TransformHierarchySystem)
- Creates Rapier heightfield colliders per chunk (Parry column-major layout)
- Resamples heights to match WebGL displacement path
- Dynamic chunk collider streaming via LOD0 enter/exit callbacks

### Functions

#### getTerrainContext(state): Map<number, TerrainEntityData>
WeakMap-based per-state terrain storage

#### setTerrainHeightmapUrl(state, entity, url): void
Sets heightmap image URL for a terrain entity (called by `heightmap` adapter)

#### setTerrainTextureUrl(state, entity, url): void
Sets diffuse texture URL for a terrain entity (called by `texture` adapter)

#### extractTerrainHeightmapImageData(terrainLOD): ImageData | null
CPU-side heightmap extraction for physics height resampling

#### resampleChunkHeightsForCollider(chunk, worldSize, maxHeight, imageData, invertWorldV): void
Rebuilds chunk height samples to match WebGL vertex displacement

### Recipes

- terrain — components: ['terrain', 'transform']; attributes: `heightmap`, `texture`

### Spawner e declive (normal vs visual)

O relevo **renderizado** pode ficar mais suave que o heightmap cru devido a **`heightSmoothing`** / spread no componente `Terrain`. O **plugin spawner** posiciona instâncias com a mesma altura “visual” que o jogador vê, mas calcula a **normal** usada para `max-slope-deg` e para `align-to-terrain` com amostras **sem** esse smoothing (heightmap bruto). Assim encostas íngremes não são subavaliadas só porque o mesh está achatado. Ver `spawner/surface.ts` e `spawner/context.md`.

<!-- /LLM:REFERENCE -->

<!-- LLM:EXAMPLES -->
## Examples

### Basic Usage

#### XML Terrain with Heightmap
```xml
<terrain
  pos="0 0 0"
  world-size="256"
  max-height="50"
  levels="6"
  resolution="64"
  heightmap="/assets/heightmap.png"
  texture="/assets/terrain_diffuse.jpg"
></terrain>
```

#### XML Terrain (default procedural)
```xml
<terrain
  pos="0 0 0"
  world-size="256"
  max-height="50"
></terrain>
```

#### JavaScript API
```typescript
import * as GAME from 'vibegame';
import { Terrain } from 'vibegame/terrain';

const entity = state.createEntity();
state.addComponent(entity, Terrain, {
  worldSize: 256,
  maxHeight: 50,
  levels: 6,
  resolution: 64,
  lodDistanceRatio: 2.0,
});
```

### Heightmap URL via Adapter

The `heightmap` and `texture` attributes are parsed by adapters, not direct component fields. They set internal URLs that the bootstrap system reads when creating the TerrainLOD instance.

```xml
<terrain heightmap="/maps/island.png" texture="/maps/sand_grass.jpg" />
```
<!-- /LLM:EXAMPLES -->
