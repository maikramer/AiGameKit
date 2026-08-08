# Racing Plugin

<!-- LLM:OVERVIEW -->

Arcade kart-racing plugin: kinematic-velocity vehicles, chase camera, track
centerline, lap/checkpoint scoring, HUD widgets, nitro, engine audio, and
weather. Auto-registered in the default plugin set.
<!-- /LLM:OVERVIEW -->

## XML surface

```html
<RaceTrack name="circuit" half-width="7" laps="3" closed="true"
           centerline="x0 z0 x1 z1 ..."></RaceTrack>
<PlayerVehicle name="hero" pos="0 0.5 0" max-speed="44" accel="28" brake="48"
               engine-brake="8" reverse-speed="14" max-steer="2.5" steer-speed="9"
               grip="0.92" ride-height="0.5"
               model-url="/assets/meshes/vehicles/kart_lod0.glb" model-yaw="180"></PlayerVehicle>
<ChaseCamera target="hero" distance="7.5" height="3" follow-lag="0.16"
             turn-lag="0.2" look-ahead="4" fov="74" fov-boost="14"></ChaseCamera>
```

- `<Vehicle>` / `<PlayerVehicle>`: `model-url` swaps the procedural chassis for a
  GLB (loaded via the engine GLTF loader, base snapped to the ground plane).
  `model-yaw` rotates the model in degrees so the nose faces +Z (Hunyuan3D kart
  GLBs are author-facing-agnostic; validate in the browser).
- GLB nodes named `wheel`/`tire`/`tyre`/`rim` (case-insensitive) spin with the
  vehicle wheelSpin. When the GLB fails to load, the procedural chassis stays.

## Race SFX bank keys (optional)

The plugin fires race-event SFX via the audio bank **only when the example
defines the key** (`getSoundDef` guard — silent no-op otherwise):

| Key              | When                                            |
|------------------|-------------------------------------------------|
| `race-countdown` | each integer second of the 3-2-1 countdown      |
| `race-go`        | countdown → racing transition                   |
| `race-lap`       | each completed lap                              |
| `race-finish`    | race finished                                   |
| `race-nitro`     | nitro activation (Shift)                        |

Engine-loop audio (RPM oscillators) is synthesized in `engine-audio.ts` and
needs no assets.

## Components

- `Vehicle` (SOA: speed/tunables/control/roll/pitch/wheelSpin/rideHeight)
- `PlayerVehicle` (player marker — camera + HUD bind)
- `Track` (centerline polyline + halfWidth + laps)
- `RaceTracker` (phase machine + per-vehicle lap/checkpoint scoring)
- `ChaseCamera` (heading-trailing follow cam, FOV boost)
- `VehicleModelUrls` / `VehicleModelYaw` (per-entity GLB chassis maps)

## Systems (in plugin order)

VehicleControl (simulation) → RaceTracker (simulation) → ChaseCameraBind →
TrackSpawn (ribbon + kerbs) → VehicleVisual (draw) → ChaseCamera (draw) →
SpeedEffects (FOV kick) → VehicleParticles (drift smoke) → EngineAudio →
PostProcessing → Nitro (boost + screen flash) → NightSky → MotionTrail →
Weather → CinematicCamera.

## Notes

- Phase machine: idle → countdown → racing → finished; `setRaceState` restarts.
- Lap detection wraps the centerline arc fraction (reversed crossings ignored).
- The visual group is a roll pivot child so roll/pitch juice never fights the
  physics transform.
