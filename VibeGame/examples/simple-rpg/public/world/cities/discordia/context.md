# cities/discordia — distritos

Shell: `../discordia.xml` (Includes). **Editar o XML do distrito**, não o shell.

| Ficheiro        | Group(s)                       | Conteúdo                                                                   |
| --------------- | ------------------------------ | -------------------------------------------------------------------------- |
| `walls.xml`     | `city.walls`                   | Muralha ±32, portões, casamatas                                            |
| `roads.xml`     | `city.roads`                   | Pad praça + `<Road>` (flatten)                                             |
| `utilities.xml` | `city.plaza`, `city.landmarks` | Poço, fogueira, tochas, santuários                                         |
| `houses.xml`    | `city.houses`                  | Casas Composition                                                          |
| `forge.xml`     | `city.forge`                   | Ferraria                                                                   |
| `barn.xml`      | `city.barn`                    | Celeiro                                                                    |
| `watch.xml`     | `city.watch`                   | Torre de vigia (GLB)                                                       |
| `chapel.xml`    | `city.chapel`                  | Capela (GLB)                                                               |
| `market.xml`    | `city.market`                  | Mercado                                                                    |
| `longhouse.xml` | `city.longhouse`               | Longhouse                                                                  |
| `skirts.xml`    | `city.skirts`                  | Anel periurbano: horta NW, arbustos N/E/S/O, clutter mercado, lenha forja  |
| `grid.xml`      | `city.grid-district`           | Compact `CityGrid` (WallRect, StreetCross, Plaza, BuildingRow, Prop, Gate) |

## Shell contracts (`discordia.xml`)

| Item             | Valor                                          |
| ---------------- | ---------------------------------------------- |
| `SpawnExclusion` | `at="0 0" radius="42"` (sync `villageZones`)   |
| `TerrainPad`     | `size="96 96" falloff="16" corner-radius="14"` |
| Gates            | Cardinal openings at wall ±32                  |

`skirts.xml` sits **outside** the wall but still on/near the pad — natural clutter so the city does not read as a flat desert plateau. Valley resources stay in `../../spawn/ring.xml` (±58), not inside the exclusion disc.

City-layout recipes: engine `VibeGame/src/plugins/city-layout/context.md`.
Radii overview: [`../../context.md`](../../context.md).
