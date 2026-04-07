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
├── components.ts         # Terrain + TerrainDebugInfo ECS components
├── systems.ts            # Bootstrap, render, physics, debug systems + query helpers
├── recipes.ts            # <terrain> recipe
├── utils.ts              # Context, heightmap/texture URL setters, height resampling
└── webgl-material.ts     # WebGL terrain material with displacement + normals
```

## Scope

- **In-scope**: Heightmap-based terrain, LOD rendering, heightfield physics colliders, chunk streaming, heightmap/texture URL configuration, runtime material tuning, debug stats, height queries
- **Out-of-scope**: Procedural generation, erosion, vegetation, water rendering

## Entry Points

- **plugin.ts**: TerrainPlugin with recipes, systems, components, config defaults and adapters (`heightmap`, `texture` URL attributes)
- **systems.ts**: Four systems — bootstrap (fixed), physics (simulation), render (draw), debug (draw) + public query helpers
- **index.ts**: Public API — `Terrain`, `TerrainDebugInfo`, `TerrainPlugin`, `terrainRecipe`, `getTerrainContext`, `TerrainEntityData`, `WebGLTerrainMaterialProvider`, `getTerrainHeightAt`, `findNearestTerrainEntity`, `setTerrainWireframe`, `reloadTerrainHeightmap`, `getTerrainStats`

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
- lodHysteresis: f32 (1.2) — LOD merge hysteresis multiplier (>1 reduces flickering)
- wireframe: ui8 (0) — wireframe rendering mode
- roughness: f32 (0.85) — material roughness (runtime-adjustable)
- metalness: f32 (0.0) — material metalness (runtime-adjustable)
- normalStrength: f32 (1.0) — normal map intensity multiplier
- skirtDepth: f32 (1.0) — depth of edge skirts to hide chunk seams
- collisionResolution: ui8 (64) — physics heightfield resolution (32/64/128)
- showChunkBorders: ui8 (0) — debug: show chunk LOD boundaries

#### TerrainDebugInfo
- activeChunks: ui32 — number of visible terrain chunks
- drawCalls: ui32 — current draw calls from terrain
- totalInstances: ui32 — total instance pool size
- geometryCount: ui32 — number of geometries
- materialCount: ui32 — number of materials
- lastUpdated: f32 — timestamp of last stats update

### Systems

#### TerrainBootstrapSystem
- Group: `fixed` (after PhysicsWorldSystem)
- Creates TerrainLOD instance with WebGLTerrainMaterialProvider
- Loads heightmap/texture from URLs set via adapters
- Applies: LOD hysteresis, normal strength, collision resolution, chunk borders
- **Hot-reload**: detects heightmap URL changes and reloads at runtime (invalidates physics)
- Disposes terrain for removed entities

#### TerrainRenderSystem
- Group: `draw` (after CameraSyncSystem)
- Updates LOD frustum culling and syncs WorldTransform position to TerrainLOD
- Applies runtime material property changes (roughness, metalness, skirtDepth, wireframe)

#### TerrainPhysicsSystem
- Group: `simulation` (after TransformHierarchySystem)
- Creates Rapier heightfield colliders per chunk (Parry column-major layout)
- Resamples heights to match WebGL displacement path
- Dynamic chunk collider streaming via LOD0 enter/exit callbacks

#### TerrainDebugSystem
- Group: `draw` (after CameraSyncSystem)
- Populates TerrainDebugInfo component with live stats from TerrainLOD.getStats()

### Public Query Helpers

#### getTerrainHeightAt(state, worldX, worldZ): number
Sample terrain height at a world position. Returns 0 if no terrain is initialized.

#### findNearestTerrainEntity(state, worldX, worldZ): number
Find the nearest initialized terrain entity by distance. Returns 0 if none available.

#### setTerrainWireframe(state, entity, enabled): void
Toggle wireframe rendering on a terrain entity at runtime.

#### reloadTerrainHeightmap(state, entity, url): void
Hot-reload the heightmap from a new URL. Invalidates physics colliders.

#### getTerrainStats(state, entity): object | null
Get live terrain statistics (activeChunks, drawCalls, totalInstances, geometries, materials).

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

#### XML Terrain with Custom Material
```xml
<terrain
  pos="0 0 0"
  world-size="512"
  max-height="80"
  roughness="0.7"
  metalness="0.1"
  normal-strength="1.5"
  skirt-depth="2.0"
  lod-hysteresis="1.5"
  collision-resolution="128"
></terrain>
```

#### XML Terrain with Debug Info
```xml
<terrain
  pos="0 0 0"
  world-size="256"
  show-chunk-borders="1"
  terrain-debug-info=""
></terrain>
```

#### JavaScript API — Height Query
```typescript
import { getTerrainHeightAt, findNearestTerrainEntity } from 'vibegame/terrain';

// Get height at player position
const height = getTerrainHeightAt(state, playerX, playerZ);

// Find nearest terrain entity
const terrainEid = findNearestTerrainEntity(state, playerX, playerZ);
```

#### JavaScript API — Runtime Wireframe Toggle
```typescript
import { setTerrainWireframe } from 'vibegame/terrain';

setTerrainWireframe(state, terrainEntity, true);  // enable
setTerrainWireframe(state, terrainEntity, false); // disable
```

#### JavaScript API — Hot-Reload Heightmap
```typescript
import { reloadTerrainHeightmap } from 'vibegame/terrain';

reloadTerrainHeightmap(state, terrainEntity, '/assets/new_heightmap.png');
// Physics colliders are automatically invalidated and rebuilt
```

#### JavaScript API — Terrain Stats
```typescript
import { getTerrainStats } from 'vibegame/terrain';

const stats = getTerrainStats(state, terrainEntity);
if (stats) {
  console.log(`Active chunks: ${stats.activeChunks}, Draw calls: ${stats.drawCalls}`);
}
```

### Heightmap URL via Adapter

The `heightmap` and `texture` attributes are parsed by adapters, not direct component fields. They set internal URLs that the bootstrap system reads when creating the TerrainLOD instance.

```xml
<terrain heightmap="/maps/island.png" texture="/maps/sand_grass.jpg" />
```
<!-- /LLM:EXAMPLES -->
