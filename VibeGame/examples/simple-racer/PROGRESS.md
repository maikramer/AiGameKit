# Simple Racer — Sunset Ridge

Arcade racer built on the VibeGame **racing** plugin: a 1.75 km circuit, five
karts, three laps, rivals that actually race you.

```bash
cd examples/simple-racer
bun install
bun run dev        # http://localhost:3020 — click once to start
```

Controls: **WASD / arrows** drive · **Space** handbrake · **Shift** nitro ·
**C** camera (chase / close / hood) · **R** restart.

## What lives where

| Piece          | File                      | Notes                                                  |
| -------------- | ------------------------- | ------------------------------------------------------ |
| Circuit layout | `src/track.ts`            | 26 control nodes with per-node width and section tag   |
| Scene          | `index.html`              | track, player, four rivals, camera, HUD                |
| Boot           | `src/main.ts`             | injects the circuit, gates the start on a click, music |
| Scenery        | `src/game/track-props.ts` | walks the spline, fits each GLB to a real size         |
| Audio bank     | `src/game/sounds.ts`      | `race-*` keys the engine fires + BGM                   |

Everything else — vehicles, AI, race director, camera, HUD, track mesh — is the
engine's `racing` plugin (`VibeGame/src/plugins/racing/`).

## Circuit

Seven sectors with one idea each: start straight → long right at Turn 1 → uphill
sweep → crest (the car gets light) → downhill → esses → hairpin → infield and a
long final left back onto the straight. Elevation runs 0 → 16 m; banking is
derived from curvature.

**Layout rule:** two arms of the circuit must stay ~25 m apart (road + both
shoulders) even where their centerlines are far from crossing.
`TrackSpline.selfOverlaps()` warns on every build with the exact arc positions —
the first draft ran the hairpin 14 m from the main straight and drew two roads
through each other.

## Assets (generated 2026-08-07)

Same manifest format as `examples/simple-rpg/sample-gameassets/`.

| Group | Manifest                                                        | Regenerate                                                            |
| ----- | --------------------------------------------------------------- | --------------------------------------------------------------------- |
| Karts | `sample-gameassets/manifests/vehicles.yaml`                     | `gameassets resume --profile game.yaml --manifest manifests/vehicles` |
| Props | `sample-gameassets/manifests/props.yaml`                        | `gameassets resume --profile game.yaml --manifest manifests/props`    |
| Audio | `sample-gameassets/manifests/audio.yaml` + `scripts/gen-sfx.sh` | `bash scripts/gen-sfx.sh`                                             |

Two things about generated models, both of which bit this example:

1. **They arrive at arbitrary scale.** Every model is fitted to a declared
   real-world size (`model-length` for karts, `height` per prop) via
   `GAME.fitModel`. Dropping one in untouched is how the first build had a road
   sign taller than the grandstand.
2. **Their heading is not the engine's.** The engine is +Z forward, +X right;
   the karts are authored 90° off, hence `model-yaw="90"`. Props are elongated
   enough for `fitModel` to measure their axis and turn them automatically.

Prop GLBs carry KTX2 textures, so `GAME.ensureKTX2LoaderReady(state)` must run
before the first load or every model fails to parse.

## Environment notes

- The music model (Stable Audio Open) needs ~5120 MiB: generate BGM with the GPU
  idle (`vramd zero` first). A worker that hits "tensors on different devices"
  stays broken until `vramd respawn text2sound`.
- `~/.local/bin/*` wrappers pointed at an old checkout; the venv shebangs were
  repointed at this monorepo.
