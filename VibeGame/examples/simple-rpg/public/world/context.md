# public/world — mapas modulares

Scene fragments loaded via `<Include src="/world/…">` from `index.html`.
**Agents: edit the domain file below, not the whole `index.html`.**

| Path                          | Contents                                              |
| ----------------------------- | ----------------------------------------------------- |
| `environment.xml`             | Sky, lights, post, audio, weather, `BiomeRegion`      |
| `cities/discordia.xml`        | City shell (`SpawnExclusion` + Includes)              |
| `cities/discordia/*.xml`      | Districts: `houses`, `utilities`, `walls`, `roads`, … |
| `cities/town-demo.xml`        | Demo town @ (420,420) — `CityGrid` + prefabs          |
| `spawn/ring.xml`              | Valley resource ring + peri-urban spawners            |
| `vegetation/crystal-vale.xml` | Biome vegetation / landmarks (no `DynamicSpawner`)    |
| `clutter/crystal-vale.xml`    | Extra props / debris per biome (mushrooms, rocks, …)  |
| `atmosphere/ambient-fx.xml`   | Ambient particles (`ground-dust`, fireflies, smoke)   |
| `creatures/enemies.xml`       | Enemy / boss `DynamicSpawner`s                        |
| `ai/npcs.xml`                 | Quest NPC entities (`name=`, `dialogue-id`)           |

## CityGrid (engine)

Inside a city XML:

```xml
<CityGrid cell="4" origin="0 0" align-to-terrain="0">
  <Street from="0 0" to="4 0" width="1"></Street>
  <Building at="2 1" prefab="house" name="city.house.a"></Building>
  <Slot at="1 1" role="well" name="city.well"></Slot>
</CityGrid>
```

Cell coords are **space-separated** (`"2 1"`). Prefabs: `house`, `market-stall`, `tower`. Or `url="/assets/models/….glb"`.

**Before merging city layout edits**, run:

```bash
vibegame analyze examples/simple-rpg/index.html
```

Catches Include/asset misses and solid footprint overlaps (buildings/walls through each other).

## Contracts (sync with `src/main.ts`)

- `name="hero"` / `name="boss"` / `name="merchant"`
- `SpawnExclusion at="0 0" radius="42"` in `cities/discordia.xml`
- Cardinal gates at wall ±32 (`RESPAWN_POINTS`)
- Quest `dialogue-id` matches JSON under `src/data/quests/`

Quest/dialogue **data** stays in `src/data/quests/` and `public/data/ai/*.yaml` — not in these Scene XMLs.
