# Water Plugin

<!-- LLM:OVERVIEW -->
Water rendering with animated sine waves, depth-based coloring from terrain heightmap, Fresnel reflections via planar reflection camera with oblique projection, deterministic foam at shallow edges, wave crest highlights, underwater fog shader driven by camera position, and a Rapier cuboid sensor collider for water contact detection (non-solid, allows player to submerge).
<!-- /LLM:OVERVIEW -->

## Layout

```
water/
├── context.md            # This file
├── index.ts              # Public exports
├── plugin.ts             # Plugin definition + config defaults
├── components.ts         # Water ECS component
├── systems.ts            # Bootstrap, render, physics systems
├── recipes.ts            # <Water> recipe (inline)
├── utils.ts              # Context, terrain heightmap/config cross-plugin lookup
├── water-material.ts     # Custom GLSL ShaderMaterial (vertex + fragment)
└── planar-reflection.ts  # PlanarReflection class (mirror camera + oblique projection)
```

## Scope

- **In-scope**: Water surface rendering, wave animation, depth coloring, planar reflections, deterministic foam, underwater fog shader, water contact detection via sensor collider
- **Out-of-scope**: Fluid simulation, buoyancy physics, volumetric water, post-processing effects

## Entry Points

- **plugin.ts**: WaterPlugin with recipe, systems, components, config defaults
- **systems.ts**: Three systems — bootstrap (fixed), render (draw), physics (simulation)
- **index.ts**: Public API — `Water`, `WaterPlugin`, `waterRecipe`, `getWaterContext`, `WaterEntityData`, `createWaterMaterial`, `PlanarReflection`

## Dependencies

- **Internal**: Core ECS, transforms (WorldTransform), rendering (MainCamera, Scene, Renderer), physics (RAPIER), terrain plugin (heightmap lookup)
- **External**: Three.js, Rapier WASM

<!-- LLM:REFERENCE -->
### Components

#### Water
- size: f32 (256) — world-space extent of the water plane (X × Z)
- waterLevel: f32 (5) — Y position used for depth calculation and reflection plane
- opacity: f32 (0.8) — surface transparency
- tintR: f32 (0.1) — water tint red channel
- tintG: f32 (0.35) — water tint green channel
- tintB: f32 (0.5) — water tint blue channel
- waveSpeed: f32 (1.0) — animation speed multiplier
- waveScale: f32 (0.3) — wave amplitude multiplier
- wireframe: ui8 (0) — wireframe rendering mode
- underwaterFogColorR: f32 (0.0) — underwater fog red channel
- underwaterFogColorG: f32 (0.05) — underwater fog green channel
- underwaterFogColorB: f32 (0.15) — underwater fog blue channel
- underwaterFogDensity: f32 (0.15) — underwater fog density

### Systems

#### WaterBootstrapSystem
- Group: `fixed` (after PhysicsWorldSystem)
- Creates PlaneGeometry mesh with custom ShaderMaterial
- Instantiates PlanarReflection (512×512 render target)
- Cross-plugin lookup for terrain heightmap/config
- Cleans up mesh + physics for removed entities

#### WaterRenderSystem
- Group: `draw` (after CameraSyncSystem)
- Updates time, camera position uniforms each frame
- Computes `uUnderwaterFade` from camera Y vs waterLevel (smooth transition over 5 units)
- Pushes underwater fog color/density uniforms from Water component
- Renders planar reflection (mirrors scene, hides water meshes, uses oblique projection)
- Reflections skipped when camera is below waterLevel
- Syncs WorldTransform position to mesh

#### WaterPhysicsSystem
- Group: `simulation` (after TransformHierarchySystem)
- Creates a Rapier cuboid sensor collider (size × 2.0 × size) at water level
- Sensor mode: player falls through water, does not walk on surface
- `isSubmerged` flag on WaterEntityData for gameplay hooks

### Functions

#### getWaterContext(state): Map<number, WaterEntityData>
WeakMap-based per-state water entity storage

#### findNearestTerrainHeightmap(state): THREE.Texture | null
Cross-plugin lookup into terrain context for heightmap texture

#### findNearestTerrainConfig(state): { worldSize, maxHeight } | null
Cross-plugin lookup for terrain world size and max height

#### createWaterMaterial(options): THREE.ShaderMaterial
Creates the water ShaderMaterial with all uniforms

### Classes

#### PlanarReflection
- `texture` getter — reflection render target texture
- `render(renderer, scene, camera, waterLevel)` — renders mirrored scene with oblique projection; skips when camera is below waterLevel
- `resize(width, height)` — updates render target size
- `dispose()` — cleans up render target

### Shader Uniforms

| Uniform | Type | Description |
|---------|------|-------------|
| uTime | float | Elapsed time for wave animation |
| uWaterLevel | float | Y coordinate of water surface |
| uOpacity | float | Surface transparency |
| uTint | vec3 | Water color tint |
| uWaveSpeed | float | Animation speed |
| uWaveScale | float | Wave amplitude |
| uShallowColor | vec3 | Shallow water color (0x2ec4b6) |
| uDeepColor | vec3 | Deep water color (0x0a1628) |
| uFoamColor | vec3 | Foam edge color (white) |
| uFresnelPower | float | Fresnel exponent (3.0) |
| uMaxDepth | float | Depth normalization range (15.0) |
| uFoamThreshold | float | Foam depth cutoff (1.5) |
| uFoamFeather | float | Foam edge softness (0.8) |
| tReflection | sampler2D | Planar reflection texture |
| tHeightMap | sampler2D | Terrain heightmap (optional) |
| uCameraPosition | vec3 | Camera world position |
| uUnderwaterFade | float | Underwater transition factor (0.0–1.0, driven by camera Y vs waterLevel) |
| uUnderwaterFogColor | vec3 | Underwater fog color (default dark blue) |
| uUnderwaterFogDensity | float | Underwater fog density (default 0.15) |

### Recipes

- water — components: ['water', 'transform']
<!-- /LLM:REFERENCE -->

<!-- LLM:EXAMPLES -->
## Examples

### Basic Usage

#### XML Water Plane
```xml
<Water   pos="0 5 0"
  size="256"
  opacity="0.8"
  wave-speed="1.0"
  wave-scale="0.3"
></Water>
```

#### XML Water with Custom Tint
```xml
<Water   pos="0 3 0"
  size="128"
  opacity="0.6"
  tint-r="0.0"
  tint-g="0.2"
  tint-b="0.6"
  wave-speed="2.0"
  wave-scale="0.5"
></Water>
```

#### JavaScript API
```typescript
import * as GAME from 'vibegame';
import { Water } from 'vibegame/water';

const entity = state.createEntity();
state.addComponent(entity, Water, {
  size: 256,
  waterLevel: 5,
  opacity: 0.8,
  tintR: 0.1,
  tintG: 0.35,
  tintB: 0.5,
  waveSpeed: 1.0,
  waveScale: 0.3,
});
```

### Combined with Terrain

Water uses terrain heightmap for depth-based coloring and foam. Place water at a Y position below terrain peaks:

```xml
<Terrain   pos="0 0 0"
  world-size="256"
  max-height="50"
  heightmap="/assets/heightmap.png"
></Terrain>

<Water   pos="0 5 0"
  size="256"
  opacity="0.8"
></Water>
```
<!-- /LLM:EXAMPLES -->
