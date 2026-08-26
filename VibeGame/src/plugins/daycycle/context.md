# daycycle

<!-- LLM:OVERVIEW -->

Day/night calendar: one `<DayCycle>` `GameClock` is the single authority for
minute-of-day/day/season/year. Drives the procedural sky (quantized sun
angles keep PMREM rebuilds to ~1 per band) and the ambient light ramp, fires
local events (DAY_ADVANCED/SEASON_CHANGED/YEAR_CHANGED) for day-driven rules
like crop growth, and renders a `<Clock>` HUD widget. Sleeping is
`sleepUntilMorning`. Opt-in — **not** in `DefaultPlugins`.
<!-- /LLM:OVERVIEW -->

Day/night calendar: a `GameClock` the simulation advances, a pure sun arc that
drives `<Sky>` and ambient light, and day/season/year events games hang their
rules on. Named `daycycle` because `chrono` is debug time-travel and `time`
collides with `state.time`.

## Layout

| File       | Responsibility |
| ---------- | -------------- |
| components | `GameClock` SoA component (minuteOfDay, day/season/year, arc shaping) |
| sun        | Pure math: `sunAngles`, `daylightFactor`, `quantizeAngle` |
| calendar   | `Season`, `SEASON_NAMES`, enum mapping (`fall` = `autumn`) |
| systems    | `DayCycleSystem` (simulation), `DayCycleSkySystem` (draw, before ProceduralSkySystem), events |
| api        | `getClockEntity`, `getTimeOfDay`, `formatClock`, `setClockScale/Paused`, `advanceGameDay`, `sleepUntilMorning` |
| hud        | `<Clock>` widget registered via `registerHudWidgetFactory` — the plugin adds a HUD widget without touching the hud plugin |
| serializer | Global save of minute/day/season/year |
| recipes    | `<DayCycle>`, `<Clock>` |

## Contracts

- **PMREM quantization is load-bearing**: `ProceduralSkySystem` rebuilds its
  environment (a full cube render) whenever `sunElevation/sunAzimuth` change.
  `DayCycleSkySystem` quantizes writes to `skyStepDeg` (default 2°) — with a
  20-minute day that is ~1 rebuild every 16 s instead of one per frame.
- **Events are a local callback registry** (`onClockEvent` +
  `DAY_ADVANCED`/`SEASON_CHANGED`/`YEAR_CHANGED`) so the plugin works in a game
  with nothing but DefaultPlugins — no rpg-core EventBus dependency.
- **Sleep flows through the same rollover**: `sleepUntilMorning` advances
  minute-by-minute math through `advanceClockMinutes`, so calendar events fire
  exactly as they would awake.
- Not in `DefaultPlugins`; saves as a global (`daycycle`) via
  `registerGlobalSaveSerializer`.

## Naming

Barrel names were chosen against verified collisions: `advanceGameDay` (vs
farm-plot's `advanceFarmDay`), `setClockScale` (`setTimeScale` is rpg-pause's),
`sleepUntilMorning`/`formatClock` (`sleep`/`formatTime` too generic / hud's).
