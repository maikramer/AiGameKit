# Water Plugin

<!-- LLM:OVERVIEW -->

Lakes and rivers that carve the shared terrain height sampler, then spawn a
water surface mesh. Carve + density + remesh follow the terrain
**ground-mutation** pipeline — water owns only the bowl/bank design profile.
<!-- /LLM:OVERVIEW -->

## Layout

```
water/
├── context.md        # This file
├── carve.ts          # Bowl / channel / river-bank profiles → height-brush
├── lake-bowl.ts      # LakeBowl WaterShape
├── river-channel.ts  # RiverChannel WaterShape
├── water-shape.ts    # Shared apply: density → carve → rebuild → mesh
├── systems.ts        # Lake/River apply (after TerrainPad)
├── registry.ts       # WaterBody queries (shore / bank / level)
├── path-utils.ts     # Polyline AABB / resample (authored paths)
└── …
```

## Ground mutation (shared with road/pad)

Order inside `applyWaterShape`:

1. **Density** — `applyCorridorDensity` (river) or `applyFeatureDensity` (lake)
   with `densityLeafPad` so chunk borders share boost.
2. **Carve** — `carveBowl` / `carveRiverChannel` via `applyHeightBrush` or
   segmented `forEachTexelInAabb` (±1 texel margin, same lattice as roads).
3. **Remesh** — `rebuildTerrainDerivatives`.
4. **Brush registry** — footprint for navmesh / queries.

Profiles: bowl `(1−t²)^1.5`; river = water bowl + bank raise + feather
(raise-then-lower so bends don't poke into neighbouring channels). Shore outline
uses `shapeRadius` so mesh, sand mask and carve agree.

## Entry

- Recipes: `<Lake>`, `<River>` (see `plugin.ts` / systems).
- Pads stamp first (`after: [TerrainPadApplySystem]`).
