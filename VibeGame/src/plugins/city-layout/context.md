# city-layout

Declarative city blocks on a cell grid. Agents edit small XMLs under
`public/world/cities/` — not the whole `index.html`.

## CityGrid

```xml
<CityGrid cell="4" origin="0 0" align-to-terrain="0">
  …
</CityGrid>
```

Cell coords: **space-separated** (`at="2 1"`). Commas become numbers in the XML parser.

## Child recipes

| Tag           | Role              | Key attrs                                       |
| ------------- | ----------------- | ----------------------------------------------- |
| `Street`      | Road segment      | `from` `to` `width` `texture-url`               |
| `StreetRing`  | Loop around rect  | `min` `max` `width` `texture-url`               |
| `StreetCross` | + through rect    | `min` `max` `width` `texture-url`               |
| `Plaza`       | Ground pad        | `min` `max` `color` `texture-url`               |
| `Wall`        | Curtain segment   | `from` `to` `height` `color` `texture-url`      |
| `WallRect`    | Box walls + gates | `min` `max` `gates` `texture-url` `gate-prefab` |
| `Gate`        | Arch at cell      | `at` `facing` `prefab`                          |
| `Building`    | One structure     | `at` `prefab` / `url` `rot` `name`              |
| `BuildingRow` | Line of buildings | `from` `to` `step` `prefab`                     |
| `Block`       | Fill / ring       | `min` `max` `mode="perimeter\|fill"` `prefab`   |
| `Prop`        | Amenity           | `at` `prefab` `collider`                        |
| `Slot`        | Named marker      | `at` `role` `name`                              |

## Prefabs

**Building:** `house`, `house-wide`, `cottage`, `market-stall`, `tower`,
`watchtower`, `chapel`, `forge`, `barn`, `longhouse`, `gate`

**Prop:** `well`, `campfire`, `flagpole`, `torch`, `shrine`, `crate`, `bench`,
`fountain`

Any of the above also accept `url="/assets/….glb"` instead of `prefab`.

## Example — compact district

```xml
<CityGrid cell="4" origin="0 0" align-to-terrain="0">
  <WallRect min="0 0" max="8 8" height="3.5" gates="n,s"></WallRect>
  <StreetCross min="0 0" max="8 8" width="1"></StreetCross>
  <Plaza min="3 3" max="5 5" color="#6b4a2b"></Plaza>
  <BuildingRow from="1 1" to="1 7" step="2" prefab="house" name="n.row"></BuildingRow>
  <Block min="6 1" max="7 3" mode="fill" prefab="cottage" name="se"></Block>
  <Prop at="4 4" prefab="well" name="plaza.well"></Prop>
  <Prop at="5 4" prefab="campfire" collider="none"></Prop>
  <Gate at="4 0" facing="s"></Gate>
</CityGrid>
```
