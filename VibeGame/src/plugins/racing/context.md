# Racing Plugin

<!-- LLM:OVERVIEW -->

Arcade kart racing: 3D circuits (`TrackSpline`), vehicles simulated in track
space, rival AI, a race director (grid → countdown → laps → flag → results), a
follow camera, tyre FX, synthesised engine audio and a complete HUD.
Auto-registered in the default plugin set; a scene only pays for it if it
declares a `<RaceTrack>` / `<PlayerVehicle>`.
<!-- /LLM:OVERVIEW -->

## XML surface

```html
<RaceTrack name="circuit" width="16" laps="3" closed="true" shoulder="3.5"
           walls="true" max-bank="9"
           centerline="x y z  x y z  …"   <!-- 3D control nodes, stride 3 -->
           widths="18 18 16 …" banks="0 4 …" sections="main turn1 …"
           road-color="#3b3e46" apron-color="#5c7f3c"
           shoulder-color="#9c8a63" wall-color="#e6e8ee"></RaceTrack>

<PlayerVehicle name="hero" driver="You" color="#e03a3a"
               model-url="/assets/meshes/vehicles/kart_lod0.glb"
               model-length="2.7" model-yaw="90"
               max-speed="52" accel="27" brake="52" engine-brake="7"
               max-steer="2.7" steer-speed="11" grip="7.5" drift-grip="0.3"
               boost="2.6" boost-recharge="0.28"></PlayerVehicle>

<AiVehicle name="rival-1" driver="Vega" skill="0.94" rubber-band="0.45"
           line-offset="0" …same vehicle attributes…></AiVehicle>

<ChaseCamera target="hero" distance="7.6" height="3.1" follow-lag="0.1"
             turn-lag="0.15" look-ahead="5" fov="74" fov-boost="13"></ChaseCamera>

<HudScreenLayer><HudWidget type="race-hud"></HudWidget></HudScreenLayer>
```

Controls: **WASD / arrows** drive, **Space** handbrake, **Shift** nitro,
**1 / 2 / 3** power-ups, **V** look back, **C** cycles the camera (chase / close / hood / orbit), and the game decides
what restarts the race (`restartRace()`).

## The one idea worth knowing

**Cars are simulated in track space, not world space.** A vehicle's state is
`(s, lateral, height, heading)` along the {@link TrackSpline}; the world pose is
rebuilt from the track frame every fixed step. Vehicles therefore have **no
rigidbody** — the plugin owns their motion outright.

That buys, in one model rather than five bolted-on effects:

- **No vertical teleports.** Arc position advances continuously and is its own
  projection hint, so a circuit that crosses over itself keeps each car on its
  own branch. (The previous build projected onto a flat XZ polyline and snapped
  cars to whichever piece of road was nearest in plan view.)
- **Barriers that cannot be tunnelled.** The wall is `|lateral| ≤ width/2 +
shoulder`, a clamp — not a collider to outrun at 200 km/h.
- **Weight transfer for free.** Slip angle, the friction circle, drift on the
  handbrake, airtime over crests, banking and the off-line grip penalty all fall
  out of the same few lines.

## Components

| Component       | Holds                                                         |
| --------------- | ------------------------------------------------------------- |
| `Vehicle`       | tunables, driver inputs, and the track-space simulation state |
| `PlayerVehicle` | tag: the car the camera and HUD follow                        |
| `AiDriver`      | skill, racing-line offset, rubber-band, stuck detection       |
| `Track`         | laps, length, width, shoulder, walls                          |
| `RaceTracker`   | lap, distance, lap times, position, wrong-way, grid slot      |
| `ChaseCamera`   | rig, smoothing state, active view mode                        |

Sidecars (bitecs holds numbers only): `getTrackSpline(entity)` for the circuit,
`addTrackObstacle(x, z, radius)` for solid scenery.

## Systems

`AiDriver → VehicleControl` (both `fixed`), then `RaceDirector → TrackSpawn`
(`simulation`), then `VehicleVisual → ChaseCamera → VehicleFx → EngineAudio`
(`draw`).

## Race flow

`idle → grid → countdown → racing → finished`, with `restartRace()` returning to
`grid`. `holdRaceOnGrid()` / `markRaceReady()` let a game stream its assets in
before the lights go out — without it the first corner arrives before the cars
are visible.

Lap counting is distance-based: `laps = floor((distance + gridS) / length)` where
`distance` accumulates the signed shortest arc delta. Reversing over the line
subtracts; it cannot be farmed.

## Ghost (personal-best hologram)

`GhostSystem` records the player's lap at 12 Hz. A faster lap replaces the
stored ghost. `GhostVisualSystem` draws a translucent kart on that recording
(time-based — same clock as the current lap). The HUD delta is distance-based:
player lap time minus the ghost's time at the same metres driven. Negative =
ahead. Games persist the payload with `serializeGhostLap` / `parseGhostLap`.

Crossing a sector boundary (8 equal arcs, `GHOST_SECTOR_COUNT`) flashes
`S{n} ±delta` for ~2.2 s — same split vs the ghost, frozen at that metre mark.

## Start lights

`StartLightsSystem` (draw) plants a gantry 12 m past pole (`GRID_FIRST_S + 12`)
so the grid looks at it. Three bulbs follow the same 3-2-1-GO clock as the HUD
(`startLightPattern`): reds light one-by-one in countdown, all green for ~0.85 s
after the flag, then off. Headless skips the mesh.

## Weekend (qualifying → race)

`RaceState.session` is `'race'` or `'qualifying'`. Qualifying is the same phase
machine with **1 lap**. When the player takes the flag the results stay up;
`beginRaceFromQualifying()` (Enter on the HUD) starts a full race on that
classification as the grid — pole is whoever set the time, not the player by
default. `restartRace()` during qualifying wipes the stored grid; after the
promotion it keeps it.

## Conditions

`RaceState.condition`: `dry` | `wet` | `night` | `storm`. Wet/storm cut asphalt
grip (~42%) and top speed, throw spray, turn the weather plugin's rain on and
shine the road. Night/storm dim directional/hemi lights, pull fog in, and light
the kart headlights (`VehicleVisualSystem`). `RaceConditionsSystem` applies the
look once the track mesh exists.

## Slipstream

A car 3.5–16 m behind another in the same lane (~2.2 m lateral) gets extra
accel and a small top-speed bump. `Vehicle.draft` is 0..1 for the HUD chip.

## Intervals + final lap

The HUD shows the live gap (seconds) to the car immediately ahead and behind,
from race distance / trailing speed (`intervalToNeighbour`). Crossing onto the
last lap flashes **FINAL LAP**. Hold **V** to look back down the road (does not
change the stored camera mode). The results table adds a **Gap** column (time
behind the winner; DNF blank).

Power-up keys are **1 / 2 / 3** only — W is throttle, Q is pause.

## Race SFX bank keys (optional)

Fired only when the game defines the key (`getSoundDef` guard, silent
otherwise): `race-countdown`, `race-go`, `race-lap`, `race-finish`. Engine and
tyre sound are synthesised in `engine-audio.ts` and need no assets.

## Viadutos

`<RaceTrack viaduct-clearance="6" pylon-spacing="30">` constrói tabuleiro
(laje + fascia) e pilares em todo o troço em que a pista voa mais alto que a
folga sobre o terreno. Pontos que só se descobrem à segunda:

- O número **tem de ser igual** ao `flatten-viaduct-clearance` do `<Road>` que
  prepara o leito, senão ou o terreno é escavado por baixo de um vão, ou o vão
  fica sem pilares.
- Os pilares são omitidos onde cairiam **em cima de outra passagem da pista**
  (o circuito do simple-racer mergulha por baixo do próprio viaduto) — a coluna
  seguinte segura o vão.
- A construção espera o terreno estar pronto (`terrainReady`): sem isso os
  pilares assentavam num plano y=0 provisório.

## Authoring gotchas

- **Corridor overlap.** Centerlines that never cross can still overlap once the
  road has width. `TrackSpline.selfOverlaps()` runs on every build and warns
  with the two arc positions; fix the layout or narrow the road there. A ≥ 5 m
  height difference counts as a deliberate flyover and is not reported.
- **Generated GLBs.** Use `model-length` (real size) and `model-yaw` (nose
  direction). Chassis models skip the automatic axis alignment in
  `fitModel` — a kart is 2.4 m wide and 2.7 m long, so a measured heading is a
  coin flip. Props, being elongated, do get aligned automatically.
- **A racing scene has no `<Player>`.** The vehicle parser calls
  `disableDefaultPlayer()` so the startup plugin does not drop a walking
  character on the start line.
- **Attract mode.** Give the player's car an `AiDriver` and the AI drives it —
  which is also how the whole race is exercised in headless tests.

## Tests

`tests/unit/racing/{spline,vehicle,race,ghost,conditions}.test.ts` — geometry,
the vehicle model (including slipstream and wet grip), a full headless race,
qualifying → race grid, ghost record/playback (including sector splits), and
condition helpers. `tests/unit/racing/start-lights.test.ts` covers the gantry
clock. `tests/unit/extras/model-fit.test.ts` covers the GLB fitting.
