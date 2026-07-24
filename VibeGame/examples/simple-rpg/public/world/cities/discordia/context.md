# cities/discordia — distritos

Shell: `../discordia.xml` (Includes). **Editar o XML do distrito**, não o shell.

| Ficheiro        | Group(s)                       | Conteúdo                                                                   |
| --------------- | ------------------------------ | -------------------------------------------------------------------------- |
| `walls.xml`     | `city.walls`                   | Muralha, portões, casamatas                                                |
| `roads.xml`     | `city.roads`                   | Pad praça + `<Road>`                                                       |
| `utilities.xml` | `city.plaza`, `city.landmarks` | Poço, fogueira, tochas, santuários                                         |
| `houses.xml`    | `city.houses`                  | Casas Composition                                                          |
| `forge.xml`     | `city.forge`                   | Ferraria                                                                   |
| `barn.xml`      | `city.barn`                    | Celeiro                                                                    |
| `watch.xml`     | `city.watch`                   | Torre de vigia (GLB)                                                       |
| `chapel.xml`    | `city.chapel`                  | Capela (GLB)                                                               |
| `market.xml`    | `city.market`                  | Mercado                                                                    |
| `longhouse.xml` | `city.longhouse`               | Longhouse                                                                  |
| `skirts.xml`    | `city.skirts`                  | Anel periurbano                                                            |
| `grid.xml`      | `city.grid-district`           | Compact `CityGrid` (WallRect, StreetCross, Plaza, BuildingRow, Prop, Gate) |

City-layout recipes: see engine `VibeGame/src/plugins/city-layout/context.md`.

Contratos: `SpawnExclusion` / portões ±32 ficam no shell `discordia.xml`.
