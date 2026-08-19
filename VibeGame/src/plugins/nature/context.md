# Nature Plugin

Rule-driven composite scatter: declares species with site conditions and
mixed groves, plans every instance from the post-carve terrain, and emits
explicit-point `SpawnGroupSpec`s consumed by the shared spawner path. The
goal is maps where practically all dressing comes from rules instead of
hand-placed or box-scattered assets.

## Files

- `plugin.ts` — `NaturePlugin` (recipe `NatureSpawner`, planner system, marker component)
- `parser.ts` — XML → `NatureRulesPlan` (validation, fail-fast errors)
- `rules.ts` — rule model (`SpeciesRule`, `WhereCondition`, `GroveRule`), bands, `matchesWhere`
- `features.ts` — `sampleSiteFeatures`: altitude/slope/biome/water/road/bank/noise per point
- `planner.ts` — `planNatureSpawns`: the deterministic 3-phase planner
- `spec-from-rules.ts` — species bucket → `SpawnGroupSpec` (explicit `points`)
- `planner-system.ts` — `NaturePlannerSystem` (setup, ground-ready gated)
- `context.ts` / `components.ts` — plan store (WeakMap) + `Nature.planned` marker

## XML

```xml
<NatureSpawner seed="9100" region-min="-660 0 -660" region-max="660 0 660"
  density-per-km2="900" min-spacing="5" noise-scale="90">

  <Species id="oak" weight="5" cap="400" profile="tree" variation="tree"
    url="/assets/meshes/forest/tree_oak_lod0.glb"
    lod1-url="…" lod2-url="…"
    scale-min="0.9" scale-max="1.8" footprint-radius="2.2" max-distance="170"
    avoid-road="1">
    <Where altitude-min="0" altitude-max="11" slope-max="28"
      road-dist-min="14" water-dist-min="6" />
  </Species>

  <Species id="rock_field" weight="1.5" variation="rock" url="…rock_mossy_lod0.glb">
    <Where slope-max="40" road-dist-min="10" noise-min="0.55" />
  </Species>

  <!-- Adjacency: own candidate budget, anchored on host instances -->
  <Species id="mushroom" weight="1.5" url="…mushroom_glow_lod0.glb">
    <Where near="oak" near-dist-min="0" near-dist-max="10" altitude-max="14" />
  </Species>

  <!-- Composite landmark: hub + members in a ring (0 = centre, 1 = edge) -->
  <Grove id="campsite" count="4" radius="8">
    <Where altitude-min="11" altitude-max="17" slope-max="14"
      road-dist-min="30" water-dist-min="16" />
    <Member species="witch_hut" count-min="1" count-max="1" />
    <Member species="campfire" count-min="1" count-max="1" at-min="0.25" at-max="0.55" />
    <Member species="crate" count-min="2" count-max="3" at-min="0.5" at-max="1" />
  </Grove>
</NatureSpawner>
```

Band conditions use `*-min` / `*-max` attribute pairs — the engine's
convention (`scale-min/max`, `count-min/max`). Range strings like
`"11..17"` cannot be used in XML: XMLValueParser pre-converts them to the
number 11 (`parseFloat`) before any plugin sees them (regression-tested in
`nature-parser.test.ts`). `parseRangeBand` remains the programmatic band
API. Omitting a side leaves it unbounded.

### Root attributes

`seed`, `region-min`/`region-max` (required, `"x y z"` — Y ignored),
`count` xor `density-per-km2` (total scatter candidates; hard cap 30k with
warn), `min-spacing` (default 2.5 m, dart-throwing spacing), `noise-scale`
(default 90 m, world metres per noise cell).

### `<Species>`

One spawnable asset. `id` + `url` (GLB) required; `lod1-url`/`lod2-url`
optional. Every other attribute is forwarded to
`resolveGroupSpawnFields` — the same vocabulary as `<StaticSpawner>`
(`profile`, `scale-*`, `footprint-radius`, `align-to-terrain`,
`ground-align`, `random-yaw`, `variation`, `max-distance`, `base-y-offset`,
…). `weight` (default 1) is the pick probability share among species whose
conditions match a site; `0` = only spawned via groves. `cap` limits total
instances. Seeds derive from `seed` + species id (stable).

### `<Where>` conditions (optional, ANDed)

| Attributes | Meaning |
| --- | --- |
| `biome="floresta,vale"` | `<BiomeRegion>` ids or type names |
| `altitude-min` / `altitude-max` | world Y band (m) |
| `slope-min` / `slope-max` | slope band (deg); max also feeds `maxSlopeDeg` |
| `water="in\|bank"` | floats on the wet surface / carved bank ring (spawner `in-water`/`near-water` anchoring); exclusive with water-dist |
| `water-dist-min` / `-max` | signed distance (m) to the waterline; negative inside water |
| `road-dist-min` / `-max` | signed distance (m) to the road carve edge; negative over the carve |
| `near="oak,pine"` + `near-dist-min`/`-max` | adjacency: distance to already-planned instances of those species |
| `noise-min` / `noise-max` | fBm mask [0,1] — organic species patches |

Conditions on absent features (no lakes, no roads) fail — the author asked
for proximity to something the world does not have.

### `<Grove>` + `<Member>`

Composite landmark: `count` hubs × members. Hub sites are dart-thrown with
`radius` spacing and filtered by the grove `<Where>` (no `near` allowed on
hubs). `Member species` must reference a `<Species id>` (order-independent:
groves may appear before their species); `count-min/max` is the per-grove
count; `at-min/at-max` is the ring placement (0 = hub centre, 1 = grove
edge, area-uniform in the annulus). Member points still honour their own
species `<Where>` (a slope-loving rock is dropped on flat grove ground).

## Planner (`planNatureSpawns`)

Deterministic (mulberry32 of `seed`), three phases:

1. **Scatter** — dart-throw spaced candidates (own grid hash, honours
   occupancy/exclusions for hand-placed areas), sample site features once
   per point, weighted pick among matching species (caps respected).
2. **Groves** — hubs + ring members, validated against species rules.
3. **Adjacency** — species with `near` conditions get a candidate budget
   proportional to their weight share and each candidate is drawn **in the
   annulus around a random host instance** (not region-wide darts — under
   canopies and shore rings are a tiny slice of the region, and uniform
   sampling starves them).

Output: one bucket of world XZ points per species →
`speciesSpawnSpec` → child entities with `SpawnerPending` →
`TerrainSpawnSystem` explicit-points mode (exact position, single
validation, drop on slope/occupancy failure).

## Integration

- `NaturePlannerSystem`: `setup`, after pads/lakes/rivers/roads,
  `before: TerrainSpawnSystem`, gated by `isGroundReadyForPlacement`
  (same pattern as `VegetationPlannerSystem`).
- Rule-derived spec defaults: `maxSlopeDeg` = `<Where slope>` max;
  `avoid-water`/`avoid-road` default **off** when the species has a
  water/road condition (the planner already placed relative to that
  feature) — set them explicitly (e.g. `avoid-road="1"` for crown-through-
  viaduct rejection); `water="in"`/`"bank"` map to `inWater`/`nearWater`.
- `NaturePlugin` ships in `DefaultPlugins` (tree-shakeable).
- Variance/instancing/LOD/alignment all come from the shared spawner path;
  this plugin only decides **where** and **which species**.
- Micro-habitats (a lakeshore ring is ~1% of a region) work best as a
  second focused `<NatureSpawner>` with a tight region and high density —
  see `examples/simple-racer/public/world/nature/forest.xml`.

## Tests

`tests/unit/nature/` — `where-conditions` (bands + matching),
`nature-parser` (validation + the XMLValueParser numeric pre-conversion
regression), `nature-planner` (determinism, altitude zones, caps, spacing,
groves, adjacency, exclusion/water/road bands), `spec-from-rules`
(defaults + points). Explicit-points placement itself:
`tests/unit/spawner/points-mode.test.ts`.
