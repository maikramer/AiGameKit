# weather

Scene-wide weather: drifting cloud billboards, optional rain sprites, wind
vector for gameplay/FX. Baseline comes from one `<Weather>` entity; biomes can
override **rain** and **clouds** while the player is inside a region.

## Recipe

```html
<Weather
  wind="0.7 0.25"
  wind-strength="1.5"
  clouds="0.5"
  cloud-height="150"
  rain="0"
  cycle="1"
  seed="12345"
></Weather>
```

| Attr            | Meaning                                                                |
| --------------- | ---------------------------------------------------------------------- |
| `wind`          | Direction XZ (normalized at runtime)                                   |
| `wind-strength` | Speed m/s                                                              |
| `clouds`        | Coverage 0..1 (baseline; cycle breathes ±~0.25 around it when enabled) |
| `cloud-height`  | Billboard height above camera Y                                        |
| `rain`          | API/cycle precipitation target 0..1                                    |
| `cycle`         | `1` = slow cloud coverage drift (~minutes)                             |
| `seed`          | Optional; deterministic cloud field layout                             |

## Runtime API (`state.ts`)

| Function                   | Role                                                             |
| -------------------------- | ---------------------------------------------------------------- |
| `setWeather(state, patch)` | Set wind / `cloudsTarget` / rain / height; values ramp over fade |
| `setEnvironmentRain`       | Biome drizzle — `effectiveRainTarget = max(api, biome)`          |
| `setEnvironmentClouds`     | Biome coverage — `≥ 0` wins over cycle; `-1` clears override     |
| `effectiveRainTarget`      | Wettest of API vs biome                                          |
| `effectiveCloudsTarget`    | Biome override when set, else `cloudsTarget`                     |
| `getWindVector`            | `direction × strength` for FX / gameplay                         |

Cloud opacity and motion use `effectiveCloudsTarget` (not raw `cloudsTarget`),
so a dense dark-forest override is not overwritten by the ambient cycle.

## Biome integration

`BiomeDetectionSystem` lerps `BiomeRegion.rain` / `BiomeRegion.clouds` and calls
`setEnvironmentRain` / `setEnvironmentClouds`. Clouds default to `-1` (inherit).
See [`../biomes/context.md`](../biomes/context.md).

simple-rpg example (`public/world/environment.xml`):

- dark-forest → `clouds="0.85"`
- desert → `clouds="0.25"`
- swamp → `rain="0.35"` (clouds inherit)

## Clouds implementation

- `clouds.ts` — instanced soft billboards (`CLOUD_COUNT`), camera-anchored, wrap
  on a ring so they recycle behind the player.
- Drift uses wind × strength; `seed` makes placement reproducible.

## Testing

- `tests/unit/weather/weather-state.test.ts` — wind normalize, rain compose,
  `environmentClouds` override / clear, `BiomeRegion` `clouds` parse + default `-1`.
