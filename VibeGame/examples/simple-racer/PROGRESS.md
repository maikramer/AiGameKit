# Simple Racer — Sunset Ridge (Crystal Vale)

Cart racing on the VibeGame **racing** plugin: a 5.45 km circuit, five wooden
carts, three laps, rivals that actually race you. Art direction matches
**Crystal Vale** (`style_preset: painterly`) — trees, houses, city gate and
vegetation are the RPG packs; only the carts are generated for this example.

```bash
cd examples/simple-racer
bun install
bun run dev        # http://localhost:3020 — click once to start
```

Controls: **WASD / arrows** drive · **Space** handbrake · **Shift** nitro ·
**1/2/3** power-ups · **V** look back · **C** camera (chase / close / hood) ·
**R** restart · **Enter** (after qualifying) start the race.

Menu: **Race** / **Time Trial** / **Weekend**, plus **Dry / Wet / Night / Storm**.
Weekend is one flying lap, then the 3-lap race on that grid. Wet cuts grip and
puts rain on the circuit; night/storm light the headlights.

## What lives where (world XML)

`index.html` é só a casca (canvas + menu + a ordem dos Includes). O mundo está
em `public/world/`, no mesmo estilo do simple-rpg:

| Ficheiro | Editar lá |
| `world/environment.xml` | céu, luz, pós-processamento, vento |
| `world/terrain.xml` | heightmap, cores, lago da bacia |
| `world/circuit/bed.xml` | terraplanagem: escapatória, berm, viaduto |
| `world/circuit/track.xml` | pista: largura, voltas, tema, pilares |
| `world/circuit/hazards.xml` | obstáculos e power-ups (coords de pista) |
| `world/city/downtown.xml` | os prédios da reta principal |
| `world/nature/forest.xml` | floresta da bacia, pinhal da montanha |
| `world/grid.xml` | jogador e rivais |
| `world/hud.xml` | câmara, música, HUD, pausa |

O traçado continua em `src/track.ts`: o `main.ts` injeta `centerline`/`path`/
`heights`/`widths`/`banks` nos `<RaceTrack>`/`<Road>` através de
`GAME.onWorldXml` — um hook que corre depois dos Includes e antes de existirem
entidades (o `document.querySelector` deixou de funcionar quando o circuito
passou a viver num include).

## What lives where

| Piece          | File                      | Notes                                                  |
| -------------- | ------------------------- | ------------------------------------------------------ |
| Circuit layout | `src/track.ts`            | 39 control nodes with per-node width and section tag   |
| Scene          | `index.html`              | track, player, four rivals, camera, HUD                |
| Boot           | `src/main.ts`             | injects the circuit, gates the start on a click, music |
| Scenery        | `src/game/track-props.ts` | walks the spline, fits each GLB to a real size         |
| Audio bank     | `src/game/sounds.ts`      | `race-*` keys the engine fires + BGM                   |

Everything else — vehicles, AI, race director, camera, HUD, track mesh — is the
engine's `racing` plugin (`VibeGame/src/plugins/racing/`).

## Circuit

Nine ideas, one per stretch of the real terrain (see the block map in
`src/track.ts`): downtown straight → long right at Turn 1 → the rim → the
flyover across the basin (~20 m over the forest) → west loop → the climb into
the mountains → summit hairpin → a descent that dives **under** the flyover →
return straight plus the stadium hairpin onto the grid. Elevation runs 6 → 30 m
and the lap is ~5.45 km; banking is derived from curvature.

### Performance (o que já mordeu)

A pista tem ~5.4 km e o cenário acompanha. Duas coisas mediram-se com o
profiler (`?profiler=1` ou `__VIBEGAME__.profiler`) enquanto se conduz:

1. **O cenário tem de ser instanciado.** A primeira versão clonava um GLB por
   prop: **1713 meshes** na cena, grupo `render` a **10.6 ms**. Passando tudo
   por `GAME.spawnInstancedGltf` (a mesma pool do `<GLTFLoader instanced>`) →
   **189 meshes**, `render` **2.9 ms**. Os postes de luz seguem o mesmo
   princípio com quatro `InstancedMesh` (um por peça) em vez de 160 grupos.
2. **Luzes pontuais só onde se vêem**: um `PointLight` por poste em 5 km são
   160 luzes para um renderer que acende as 12 mais próximas — agora só a
   cidade e o grampo do estádio recebem luz real.

Depois disto o custo por frame do jogo vive quase todo no `render` + GPU; os
sistemas somam ~1.5 ms. Se voltar a cair, medir primeiro (`profiler.top(10)`)
antes de mexer.

### The ground under the circuit

`main.ts` feeds the shared `<Road flatten>` carver four parallel lists sampled
off the same spline: `path`, `heights`, `widths` and `banks`. That inverts the
default relationship — instead of surveying the terrain and terracing whatever
is there, the carver grades the ground to the **authored** track elevation, so
the ribbon sits exactly `TRACK_ELEVATION` above the bed for the whole lap and
the wall footing never floats.

The cross-section is authored on the `<Road>` tag: bed (`widths`, = racing width

- `BED_MARGIN`) → gravel run-off (`flatten-shoulder`) → berm that catches a car
  (`flatten-berm`) → embankment (`flatten-falloff`). `flatten-closed="1"` wraps
  the profile through the start/finish line, and `flatten-bank="1"` tilts the bed
  with the banking so a peralted corner sits in a tilted shelf.

Changing the layout in `src/track.ts` is enough — everything above is derived.

**Layout rule:** two arms of the circuit must stay ~25 m apart (road + both
shoulders) even where their centerlines are far from crossing.
`TrackSpline.selfOverlaps()` warns on every build with the exact arc positions —
the first draft ran the hairpin 14 m from the main straight and drew two roads
through each other.

## Assets

Forest / village / infra manifests are **shared** with simple-rpg
(`examples/shared-assets/`). Copy the RPG GLBs (no GPU):

```bash
bash ../shared-assets/sync-from-rpg.sh
```

Carts are the only GPU step for this example (`style_preset: painterly`):

| Group                    | Manifest                                                        | Regenerate                                                                                    |
| ------------------------ | --------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| Carts                    | `sample-gameassets/manifests/vehicles.yaml`                     | `cd sample-gameassets && gameassets resume --profile game.yaml --manifest manifests/vehicles` |
| Forest / village / infra | symlinks → `examples/shared-assets/manifests/`                  | generate in the RPG, then `sync-from-rpg.sh`                                                  |
| Audio                    | `sample-gameassets/manifests/audio.yaml` + `scripts/gen-sfx.sh` | `bash scripts/gen-sfx.sh` (race SFX stays; do not mix RPG BGM)                                |

Handoff writes into `../public` (`output_dir: ../../public/assets` from
`sample-gameassets/`). `model-yaw="90"` — as carroças nascem deitadas ao longo
de X (bbox 2.40 × 1.68 × 1.14), e o motor **não** roda veículos pelo PCA
(`normaliseModel` usa `minElongation: 99`: heading é só do autor), por isso o
yaw 90 é o que põe o eixo comprido em +Z. Empilhar um auto-yaw em cima disso —
ou deixar o yaw a 0 — põe o comprimento de través (panqueca na pista).
`size_m` Omni é `[comprimento X, altura Y, largura Z]`.

Two things about generated models, both of which bit this example:

1. **They arrive at arbitrary scale.** Every model is fitted to a declared
   real-world size (`model-length` for carts, `height` per prop) via
   `GAME.fitModel`. Dropping one in untouched is how the first build had a road
   sign taller than the grandstand.
2. **Their heading is not the engine's.** +Z forward, +X right. Vehicles are
   author-only: nothing rotates them but `model-yaw`, so the value has to
   cancel the generator's arbitrary facing. Props still auto-align from
   elongation (`minElongation` default 1.08).

The finish gantry is the RPG `city_gate_arch` (pack `meshes/infra`), scaled
across the city straight with `measureProp(..., startFrame.width + 8, 'width',
'across')` — native arch is 10×5.5×1.2 m with an ~8 m opening; the straight is
22 m wide.

Prop GLBs carry KTX2 textures, so `GAME.ensureKTX2LoaderReady(state)` must run
before the first load or every model fails to parse.

## Environment notes

- The music model (Stable Audio Open) needs ~5120 MiB: generate BGM with the GPU
  idle (`vramd zero` first). A worker that hits "tensors on different devices"
  stays broken until `vramd respawn text2sound`.
- `~/.local/bin/*` wrappers pointed at an old checkout; the venv shebangs were
  repointed at this monorepo.
