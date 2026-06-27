# biomes

Detects the player's biome region each frame and applies an interpolated
fog / ambient-light / BGM crossfade while the player crosses biome borders.
Backed by the `simple-rpg` biomas design (floresta sombria, deserto, pântano)
over a single 10 km terrain with no loading.

## Components (SOA, MAX_ENTITIES)

- `BiomeRegion` (`biome-region`) — one entity per `<BiomeRegion>` element.
  Holds the AABB (`polyMinX..polyMaxZ`), `type` (0=vale, 1=floresta, 2=deserto,
  3=pântano), tint/fog/ambient overrides and `bgmLayer`. The polygon vertex list
  is variable-length so it lives in the parser WeakMap, not the SOA.
- `ActiveBiome` (`active-biome`) — singleton on the player. `current`/`target`
  hold BiomeRegion entity ids or `NO_BIOME` (NULL_ENTITY) for the default vale;
  `blend` is the 0..1 crossfade progress.

## Recipe

```html
<BiomeRegion
  id="dark-forest"
  type="1"
  polygon="-300 80, 300 80, 300 400, -300 400"
  tint="#1a3320"
  fog-color="#0a1815"
  fog-density="0.04"
  ambient="#3a4a55"
  bgm-layer="2">
</BiomeRegion>
```

All attributes are consumed by `biomeRegionParser` (listed in
`parserAttributes`), which applies defaults then runs the adapters in
`adapters.ts` and registers the region (vertices + AABB) in the per-state
WeakMap queried by `findBiomeRegionAt`.

## System — BiomeDetectionSystem (group `late`)

Per frame, after player movement:

1. Locate the player via `defineQuery([PlayerController])` and lazily attach
   `ActiveBiome` (`current=target=NO_BIOME`, `blend=1`) on first contact.
2. AABB broad-phase + point-in-polygon (ray casting) narrow-phase via
   `findBiomeRegionAt`. Outside every region → target `NO_BIOME` (vale).
3. On target change: reset `blend=0` and fire one `crossfadeMusicLayers`
   (duration `BIOME_BLEND_DURATION` = 0.5 s). The audio mixer drives its own
   internal fade; the biome system does not write music weights per frame.
4. Advance `blend += dt / 0.5` (clamped 1) and write lerped fog/ambient.
   When `blend` reaches 1, `current = target`. **Visual writes only happen
   while a blend is in progress** — no per-frame mutation when settled.

## Integration points (other plugins)

- **Postprocessing** — fog override written to the first entity with
  `heightFog == 1` (`fogColor` packed 0xRRGGBB, `fogDensity`). If none exists,
  fog override is skipped with a one-shot warning.
- **rendering / AmbientLight** — ambient override written to the first
  `AmbientLight` entity's `skyColor` (groundColor/intensity untouched). Maps the
  region's single `ambient` RGB to the hemispheric sky color. Skipped with a
  one-shot warning if absent.
- **audio / mixer** — `crossfadeMusicLayers(state, fromLayer, toLayer, dur)`.
  `bgmLayer` is passed straight to the mixer (0=explore, 1=battle, 2..4 custom).

The scene's pre-biome fog/ambient values are captured once (lazily, before the
first biome write) as the "vale baseline" so blending back out of a region
restores them.

## Save / load

`ActiveBiome.current` (player eid) is the only persistent field — wire it into
the existing SaveLoadPlugin snapshot in Track B (`biome: { current }`). Defaults
to `NO_BIOME` for back-compat with old saves.

## Testing

- `tests/unit/plugins/biomes/polygon.test.ts` — `parsePolygonString` +
  `pointInPolygon` (4-vertex square, 6-vertex L-shape, AABB boundary cases).
- `tests/unit/plugins/biomes/detection.test.ts` — `findBiomeRegionAt` (AABB
  true positive/negative, inside-polygon vs outside-polygon) and `advanceBlend`
  (dt increments, clamps at 1).
