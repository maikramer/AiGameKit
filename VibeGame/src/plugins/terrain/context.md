# Terrain Plugin

<!-- LLM:OVERVIEW -->
ECS-native terrain with heightmap displacement, quadtree LOD, a single terrain-wide Rapier heightfield collider, and async heightmap loading. Each chunk is a separate ECS entity for visual LOD; physics uses one heightfield per terrain field at `collisionResolution`.
<!-- /LLM:OVERVIEW -->

## Layout

```
terrain/
├── context.md            # This file
├── index.ts              # Public exports
├── plugin.ts             # Plugin definition + config defaults/adapters
├── components.ts         # Terrain + TerrainChunk + TerrainDebugInfo ECS components
├── systems.ts            # Bootstrap, LOD select, mesh, physics, debug systems + query helpers
├── recipes.ts            # <Terrain> recipe
├── utils.ts              # Context, mesh/collider registries, URL setters, height resampling
├── height-sampler.ts     # CPU height sampler (flat or heightmap-backed), bilinear interpolation
├── chunk-geometry.ts     # BufferGeometry builder from sampler per chunk
├── lod-select.ts         # Pure-function quadtree LOD selection
├── terrain-data-loader.ts # Terrain3D JSON data loader + lake/river water spawning
└── lake-renderer.ts      # XML generator for lake/river water entities
```

## Scope

- **In-scope**: Heightmap-based terrain, quadtree LOD rendering, single terrain-wide Rapier heightfield physics, async heightmap loading, runtime wireframe toggle, height queries, debug stats, hot-reload
- **Out-of-scope**: Procedural generation, erosion, vegetation, water rendering

## Entry Points

- **plugin.ts**: TerrainPlugin with recipes, systems, components, config defaults and adapters (`heightmap`, `texture` URL attributes)
- **systems.ts**: Five systems — bootstrap (fixed), LOD select (draw), mesh (draw), physics (simulation), debug (draw) + public query helpers
- **index.ts**: Public API — components, plugin, recipe, context helpers, height queries, wireframe toggle, heightmap reload, stats

## Dependencies

- **Internal**: Core ECS, transforms (WorldTransform), rendering (MainCamera, Scene, Renderer), physics (RAPIER via `getWorld()`)
- **External**: Three.js, Rapier WASM (`@dimforge/rapier3d-simd-compat`)

<!-- LLM:REFERENCE -->
### Components

#### Terrain (field entity — 1 per terrain)
- worldSize: f32 (256) — world-space extent (X × Z)
- maxHeight: f32 (50) — maximum height displacement
- levels: ui8 (6) — quadtree LOD depth
- resolution: ui8 (64) — base vertices per chunk side
- lodDistanceRatio: f32 (2.0) — split distance multiplier
- lodHysteresis: f32 (1.2) — merge hysteresis multiplier
- wireframe: ui8 (0) — wireframe rendering
- roughness: f32 (0.85) — material roughness
- metalness: f32 (0.0) — material metalness
- normalStrength: f32 (1.0) — normal intensity
- skirtDepth: f32 (1.0) — seam skirt depth
- skirtWidth: f32 (0.015625) — seam skirt UV width
- heightSmoothing: f32 (0.35) — displacement smoothing blend
- heightSmoothingSpread: f32 (1.25) — smoothing texel spread
- baseColor: ui32 (0x4a7a3a) — albedo tint
- collisionResolution: ui8 (64) — physics heightfield grid resolution
- showChunkBorders: ui8 (0) — debug chunk borders
- snowHeight: f32 (0.75) — height-based snow threshold
- colorHigh: ui32 (0xffffff) — snow/peak color
- colorMid: ui32 (0x7a9a4a) — mid-slope color
- colorLow: ui32 (0x4a6a2a) — grass/valley color
- colorRock: ui32 (0x808080) — cliff rock color
- slopeThreshold: f32 (0.55) — slope angle for rock texture
- slopeSoftness: f32 (0.1) — slope blend softness

#### TerrainChunk (N entities — dynamically spawned/despawned)
- field: ui32 — parent Terrain field entity
- originX: f32 — chunk center X in field-local space
- originZ: f32 — chunk center Z in field-local space
- size: f32 — chunk world-space extent
- level: ui8 — LOD level (0 = highest detail)
- resolution: ui8 — mesh resolution for this level
- meshDirty: ui8 — flag: geometry needs rebuild

#### TerrainDebugInfo
- activeChunks, drawCalls, totalInstances, geometryCount, materialCount, failedColliderChunks, lastUpdated

### Systems

#### TerrainFieldBootstrapSystem (fixed)
- Creates flat HeightSampler immediately (terrain appears at y=0)
- If heightmap URL set, fires async `loadHeightmapFromUrl` → replaces sampler with real heights, marks chunks dirty
- Disposes terrain for removed entities

#### TerrainLodSelectSystem (draw, after CameraSyncSystem)
- Pure-function quadtree (`selectChunks()`) against camera position
- Diffs desired chunks with existing, spawns/despawns TerrainChunk entities
- Resolution halves per LOD level (min 4)

#### TerrainMeshSystem (draw)
- For each chunk with meshDirty=1: builds BufferGeometry from sampler, creates/updates THREE.Mesh in registry
- Uses MeshStandardMaterial with field's roughness/metalness/baseColor

#### TerrainPhysicsSystem (simulation)
- Builds a single terrain-wide heightfield collider per field entity (not per-chunk)
- Uses `Terrain.collisionResolution` for physics grid; writes heights directly in Rapier column-major format
- Falls back to a thin box collider for flat terrain (no heightmap data)
- Adds `contactSkin(0.1)` for tunneling prevention without CCD
- Rebuilds collider on heightmap reload; cleans up on entity destruction

#### TerrainDebugSystem (draw, after CameraSyncSystem)
- Populates TerrainDebugInfo from chunk counts

### Public Query Helpers

- `getTerrainHeightAt(state, worldX, worldZ)` — bilinear sample from HeightSampler
- `findNearestTerrainEntity(state, worldX, worldZ)` — nearest field entity
- `setTerrainWireframe(state, entity, enabled)` — toggle wireframe on all chunks
- `reloadTerrainHeightmap(state, entity, url)` — async load new heightmap, rebuild meshes + physics collider
- `getTerrainStats(state, entity)` — live chunk/collider counts

### Recipes

- terrain — components: ['terrain', 'transform']; adapters: `heightmap`, `texture`, `base-color`, `color-high`, `color-mid`, `color-low`, `color-rock`

### Spawner e declive (normal vs visual)

O **plugin spawner** posiciona instâncias com a altura do sampler e calcula **normais** via diferenças finais sem smoothing. Ver `spawner/surface.ts`.

<!-- /LLM:REFERENCE -->

<!-- LLM:EXAMPLES -->
## Examples

### XML Terrain with Heightmap
```xml
<Terrain pos="0 0 0" world-size="256" max-height="50" levels="6" resolution="64"
  heightmap="/assets/heightmap.png" texture="/assets/terrain_diffuse.jpg"></Terrain>
```

### XML Terrain with Custom Colors
```xml
<Terrain pos="0 0 0" world-size="512" max-height="80" roughness="0.7" metalness="0.1"
  collision-resolution="128" base-color="#3d6b32" color-high="#ffffff"
  color-mid="#7a9a4a" color-low="#4a6a2a" color-rock="#808080"></Terrain>
```

### JavaScript API
```typescript
import { getTerrainHeightAt, setTerrainWireframe, reloadTerrainHeightmap } from 'vibegame/terrain';

const height = getTerrainHeightAt(state, playerX, playerZ);
setTerrainWireframe(state, terrainEntity, true);
reloadTerrainHeightmap(state, terrainEntity, '/assets/new_heightmap.png');
```
<!-- /LLM:EXAMPLES -->
