# Terrain Plugin

<!-- LLM:OVERVIEW -->

ECS-native terrain with heightmap displacement, quadtree LOD, per-chunk Rapier heightfield colliders, and async heightmap loading. Each chunk is a separate ECS entity for visual LOD and physics; colliders are built per active chunk, not as one terrain-wide heightfield. Ground appearance: biome splat layers, lake/river sand shores, height/slope tint, and procedural **noise sand** overlays (world-XZ fBm) to break up uniform grass.
<!-- /LLM:OVERVIEW -->

## Layout

```
terrain/
├── context.md            # This file
├── index.ts              # Public exports
├── plugin.ts             # Plugin definition + config defaults/adapters
├── components.ts         # Terrain + TerrainChunk + TerrainPad + TerrainDebugInfo
├── systems.ts            # Bootstrap, LOD select, mesh, physics, debug systems + query helpers
├── pad-systems.ts        # <TerrainPad> flatten + density override (setup)
├── flatten.ts            # Rounded-rect flatten helper
├── height-brush.ts       # Shared texel stamp + rebuildTerrainDerivatives
├── corridor.ts           # Polyline nearest/AABB (road + river share)
├── ground-mutation.ts    # Density leaf-pad + corridor/feature density stamps
├── density-map.ts        # Per-tile mesh density boost (featured regions)
├── brush-registry.ts     # Ground brushes (pad/lake/river/road footprints)
├── recipes.ts            # <Terrain> + <TerrainPad> recipes
├── utils.ts              # Context, mesh/collider registries, URL setters, height resampling
├── height-sampler.ts     # CPU height sampler + getGroundHeight (density-aware lattice)
├── chunk-geometry.ts     # BufferGeometry builder from sampler per chunk
├── lod-select.ts         # Quadtree LOD + effectiveResolution + meshSurfaceResolutionForPoint
├── terrain-data-loader.ts # Terrain3D JSON data loader + lake/river water spawning
```

## Scope

- **In-scope**: Heightmap-based terrain, quadtree LOD rendering, per-chunk Rapier heightfield physics, async heightmap loading, settlement pads (`<TerrainPad>`), ground appearance (splat / shore sand / height tint / noise-sand overlay), runtime wireframe toggle, height queries, debug stats, hot-reload
- **Out-of-scope**: Full procedural heightmap generation, erosion, vegetation, water rendering (carve lives in `water` / `road` plugins that mutate the same sampler)

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
- skirtDepth: f32 (1.0) — seam skirt depth (apron under residual geometric gaps)
- skirtWidth: f32 (0.015625) — seam skirt UV width
- **Frontier normals** (`chunk-geometry.ts`): lighting seams need **identical
  frontier normals** on both chunks — border verts sample the shared heightfield
  with a field-constant world-space ε (never own-chunk stencils). No height
  morph / overlap push / relief seal.
- **Density-forced LOD split** (`lod-select.ts`): road/river/pad boost that
  cannot refine lattice step without subdividing forces deepest leaves (else
  coarse chords cut above carved beds → sand shows through transparent road
  decals at chunk edges). Ribbon clears centerline mesh ±2 coarser LODs.
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
- heightBlendStrength: f32 (0.35) — height/slope colour tint mix
- aoStrength: f32 (0.85) — NAR AO multiply strength
- noiseSandStrength: f32 (0.4) — procedural sand overlay (0 = off); world-XZ fBm patches on flat mid/low ground
- noiseSandScale: f32 (0.014) — fBm frequency (higher = smaller patches)
- noiseSandThreshold: f32 (0.58) — fBm cutoff (higher = sparser)
- noiseSandHeightMin: f32 (0.02) — normalised height band start for sand patches
- noiseSandHeightMax: f32 (0.48) — normalised height band end for sand patches

### Noise sand overlay (first procedural detail layer)

Breaks up flat grass without spending a 5th biome splat channel. Shader path in `systems.ts` (`noiseSandMask` → merged into `sandMask` with lake/river shores).

| XML attr                | Component field      | Default | Meaning                                        |
| ----------------------- | -------------------- | ------- | ---------------------------------------------- |
| `noise-sand-strength`   | `noiseSandStrength`  | `0.4`   | Mix strength (`0` = off)                       |
| `noise-sand-scale`      | `noiseSandScale`     | `0.014` | World-XZ fBm frequency (↑ = smaller patches)   |
| `noise-sand-threshold`  | `noiseSandThreshold` | `0.58`  | fBm cutoff (↑ = sparser)                       |
| `noise-sand-height-min` | `noiseSandHeightMin` | `0.02`  | Normalised height band start (`y / maxHeight`) |
| `noise-sand-height-max` | `noiseSandHeightMax` | `0.48`  | Normalised height band end                     |

**Behaviour**

- fBm = 2-octave value-noise on `vWorldXZ` (`hash21` / `valueNoise` / `fbm2` in the fragment shader — reusable for future layers: gravel, moss, …).
- Gated by altitude band + flatness (same slope knobs as rock tint) so cliffs/peaks stay grassy/rocky.
- Reuses the procedural shore sand albedo/NAR; **independent** of `uSandBlend` (lakes/rivers).
- Uniforms synced by `TerrainHeightColorSyncSystem` (same dirty-gate path as height tint).

**Learnings**

- GLSL reserved word: never name a local `patch` (tessellation keyword) — use `sandPatch`.
- Do not bake this into the biome splat: shore sand already fills the “extra surface” budget; noise overlays keep splat for biomes.
- Next layers: same pattern — mask from `fbm2`, gate by height/slope, blend a detail albedo, expose 1–2 strength/scale attrs with soft defaults.

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

- Builds a Rapier heightfield collider per active chunk (not one terrain-wide collider)
- Uses each chunk's LOD resolution for the physics grid; writes heights in Rapier column-major format
- Falls back to a thin box collider for flat terrain (no heightmap data)
- Adds `contactSkin(0.1)` for tunneling prevention without CCD
- Rebuilds chunk colliders on heightmap reload; cleans up on entity destruction

#### TerrainDebugSystem (draw, after CameraSyncSystem)

- Populates TerrainDebugInfo from chunk counts

### Public Query Helpers

- `getGroundHeight(state, worldX, worldZ)` — mesh lattice + small footprint max (preferred for placement/spawning); resolution follows `meshSurfaceResolutionForPoint`
- `getTerrainHeightAt(state, worldX, worldZ)` — analytic bilinear sample from HeightSampler (single point)
- `meshSurfaceResolutionForPoint(baseRes, levels, density, localX, localZ)` — lattice res matching rendered leaf (`maxBoostOverAabb` on deepest leaf AABB, same as chunk mesh)
- `deepestLeafAabb(worldSize, levels, localX, localZ)` — field-local AABB of that leaf
- `effectiveResolution(baseRes, level, boost)` — LOD res × density factor (capped at base)
- `findNearestTerrainEntity(state, worldX, worldZ)` — nearest field entity
- `setTerrainWireframe(state, entity, enabled)` — toggle wireframe on all chunks
- `reloadTerrainHeightmap(state, entity, url)` — async load new heightmap, rebuild meshes + physics collider
- `getTerrainStats(state, entity)` — live chunk/collider counts

### Ground-mutation pipeline (pads / lakes / rivers / roads)

One shared stack — feature plugins own only the **design profile**, then call terrain helpers:

1. **Design profile** — terrace (`road/carve`), bowl/bank (`water/carve`), pad plane (`flatten`).
2. **Density stamp** — `applyCorridorDensity` / `applyFeatureDensity` (+ `densityLeafPad` = half deepest leaf so chunk borders share boost).
3. **Stamp sampler** — `applyHeightBrush` (primary stamp + cell-aware `guardAt` clamp) or segmented `forEachTexelInAabb` (±1 texel margin always).
4. **Remesh / collider** — `rebuildTerrainDerivatives` (meshDirty + Rapier + BVH + callbacks).

Ribbon/water meshes sample **analytic** `sampleHeightAt` after carve — never mesh-catchup onto LOD geometry. Polyline nearest/AABB live in `corridor.ts`.

**Cell-aware clamp** (`HeightBrush.guardAt`): the primary stamp describes the design surface at texel **centres**, but every consumer (chunk mesh, Rapier heightfield, ribbon) reconstructs by bilinear interpolation — one texel's value influences every point up to one texel away per axis (`texelInfluenceReach` = √2·step, the 2×2 bilinear stencil). In a deep cut, the first texel outside the full-weight band holds `natural + (design − natural)·w` — metres above the bed in mountain terrain — and its stencil lifts the reconstructed ground over the bed edge: terrain poking through the road/track exactly at the rim. Brushes that expose `guardAt` (road corridor, bridge clearance, pad core) clamp those neighbours to the design surface evaluated at the stencil's corridor-facing edge (`dist − texelInfluenceReach`). The clamp is **lower-only** (valley fills and flats never move), dynamic per texel (acts only where the neighbour actually sits above the design), and journalled by `owner` exactly like the primary stamp.

**Corridor helpers** (`corridor.ts`) — shared by every polyline carver:

| Helper                                      | Why                                                                                                                                                                                                             |
| ------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `nearestOnPolyline`                         | O(segments) nearest + **signed** side (`+` = driver's right, engine right vector `(tz,-tx)`) + arc position.                                                                                                    |
| `createCorridorIndex` / `nearestOnCorridor` | Uniform-grid bucket of segments. A race circuit is hundreds of segments over most of the field: the naive stamp is O(texels × segments) and hitches for seconds. Carvers switch to the index past ~24 segments. |
| `nearestCorridorPasses`                     | Every **distinct pass** of the corridor near a point (separated by arc), so an overpass / hairpin does not let one arm bulldoze the other.                                                                      |
| `resampleNodeValues`                        | Maps authored per-node lists (widths, design heights, banks) from the authored polyline onto the smoothed + resampled one, by arc fraction.                                                                     |
| `pathArcs`                                  | Cumulative arc per node.                                                                                                                                                                                        |

**Idempotent re-carve** (`height-brush.ts`): `applyHeightBrush(sampler, brush, { owner })` journals the texels it wrote; `revertHeightBrush(sampler, owner)` puts them back. Carvers that re-run (a road regrades whenever a neighbour carves) must revert before re-surveying, or a terrace reads back the terrain it already flattened and the bed creeps down every pass. Owners are independent, but a revert also discards whatever another carver wrote on those texels afterwards — revert and re-stamp in the same pass.

### Density map (featured mesh)

`DensityMap` scores height variance per tile; features stamp boost 255 via the helpers above so leaf chunks refine carves and skirts. Without boost, every LOD level keeps a ~`worldSize/baseResolution` lattice (≈31 m at 2000/64) — features narrower than that never appear on the mesh.

| Source                              | When boost is stamped                      |
| ----------------------------------- | ------------------------------------------ |
| Height variance (`buildDensityMap`) | After heightmap load                       |
| `<Lake>` / `<River>`                | Before carve (`applyWaterShape`) + leafPad |
| `<Road flatten>`                    | Before corridor carve + leafPad            |
| `<TerrainPad>`                      | After flatten, core+falloff AABB + leafPad |

Spawn/place must sample with `meshSurfaceResolutionForPoint` (wired into `getGroundHeight` + spawner `surface.ts`). Classic bugs: (1) city gate pad/road density → fine skirt, coarse spawn; (2) point-only `boostAt` while chunk uses leaf `maxBoostOverAabb` → sparse floats on dune variance tiles.

### Recipes

- `terrain` — components: ['terrain', 'transform']; adapters: `heightmap`, `texture`, `base-color`, …
- `TerrainPad` — settlement flatten (see below)

### `<TerrainPad>` — settlement flatten

Levels a rounded rectangle into the shared height sampler so buildings sit flush without a hard cliff at the edge.

```xml
<TerrainPad
  at="0 0"
  size="96 96"
  falloff="16"
  corner-radius="14"
></TerrainPad>
```

| Attribute       | Meaning                                                                |
| --------------- | ---------------------------------------------------------------------- |
| `at`            | Centre XZ (also sets `Transform`)                                      |
| `size`          | Full width × depth (metres); stored as half-extents                    |
| `falloff`       | Blend distance outside the core (larger = softer, less artificial pad) |
| `corner-radius` | Rounded corners                                                        |
| `height`        | Optional absolute Y; omit → sample terrain height at pad centre        |

**Order:** `TerrainPadApplySystem` (`setup`) stamps **before** lake/river carve (`after: [TerrainPadApplySystem]` on water). Spawner waits via `isGroundMutationPending` and runs after pad/water/road. Road flatten **skips pad cores** (`skipAt` / `pointInAnyPadCore`) so overlapping plaza arteries cannot trench the settlement floor under the CCT.

**Density:** pad stamps `applyOverride` on core+falloff (255) + `refreshChunkResolutions` — same contract as road/river. Skirt blend then shows on leaf meshes; spawners that ignore density still float (see Density map above).

`registerGroundMutationCallback` fires from `rebuildTerrainDerivatives` so already-spawned props can resync Y.

**Ground revision:** `fireGroundMutationCallbacks` and `fireHeightmapReloadCallbacks` also bump a monotonic counter read with `getGroundRevision(state)`. Systems that re-query the same (x, z) on every fixed step cache the sampled height against it and skip the probe while the number holds — `getGroundHeight` is a five-point Catmull-Rom cross over the LOD lattice, far too expensive to repeat 50x/s per entity (see `spawn-gate` `CharacterUnburySystem`). Any new code path that edits `sampler.data` directly must route through one of those two fires, or stationary consumers will keep the stale height.

### Spawner e declive (normal vs visual)

O **plugin spawner** posiciona com lattice density-aware (`sampleTerrainSurface`) e calcula **normais** no heightmap analítico. Ver [`../spawner/context.md`](../spawner/context.md) § Amostragem.

<!-- /LLM:REFERENCE -->

<!-- LLM:EXAMPLES -->

## Examples

### XML Terrain with Heightmap

```xml
<Terrain pos="0 0 0" world-size="256" max-height="50" levels="6" resolution="64"
  heightmap="/assets/heightmap.png" texture="/assets/terrain_diffuse.jpg"></Terrain>
```

### Settlement pad (city / village)

```xml
<TerrainPad at="0 0" size="96 96" falloff="16" corner-radius="14"></TerrainPad>
```

### XML Terrain with Custom Colors

```xml
<Terrain pos="0 0 0" world-size="512" max-height="80" roughness="0.7" metalness="0.1"
  collision-resolution="128" base-color="#3d6b32" color-high="#ffffff"
  color-mid="#7a9a4a" color-low="#4a6a2a" color-rock="#808080"></Terrain>
```

### Noise sand patches (procedural detail)

```xml
<Terrain
  heightmap="/assets/terrain/heightmap.png"
  texture="/assets/textures/vale_grass.png"
  noise-sand-strength="0.45"
  noise-sand-scale="0.012"
  noise-sand-threshold="0.56"
  noise-sand-height-min="0.02"
  noise-sand-height-max="0.50"
></Terrain>
```

Omit the attrs to use plugin defaults; set `noise-sand-strength="0"` to disable.

### JavaScript API

```typescript
import { getGroundHeight, getTerrainHeightAt, setTerrainWireframe, reloadTerrainHeightmap } from 'vibegame/terrain';

const height = getGroundHeight(state, playerX, playerZ);
const analytic = getTerrainHeightAt(state, playerX, playerZ);
setTerrainWireframe(state, terrainEntity, true);
reloadTerrainHeightmap(state, terrainEntity, '/assets/new_heightmap.png');
```

<!-- /LLM:EXAMPLES -->
