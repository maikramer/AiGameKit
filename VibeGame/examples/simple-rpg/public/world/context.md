# public/world — mapas modulares

Scene fragments loaded via `<Include src="/world/…">` from `index.html`.
**Agents: edit the domain file below, not the whole `index.html`.**

| Path                        | Contents                                                        |
| --------------------------- | --------------------------------------------------------------- |
| `environment.xml`           | Sky, lights, post, audio, weather, `BiomeRegion`                |
| `cities/discordia.xml`      | City shell (`SpawnExclusion` + `TerrainPad` + Includes)         |
| `cities/discordia/*.xml`    | Districts: `houses`, `utilities`, `walls`, `roads`, `skirts`, … |
| `cities/town-demo.xml`      | Demo town @ (420,420) — `CityGrid` + prefabs                    |
| `spawn/ring.xml`            | Valley resource ring ±58, river, bridges, peri-urban carpet     |
| `paths/network.xml`         | Cobble `<RoadNetwork>` cruz + anel periurbano + 4 biomes (~2 m) |
| `paths/trails.xml`          | Dirt/sand spur `<Road flatten="0">` to landmarks                |
| `vegetation/<biome>.xml`    | Carpet + canopy + rocks, one file per cardinal wedge            |
| `landmarks/<biome>.xml`     | Destinations: outposts, ruins, mine, arena, boss glades         |
| `frontier/ridges.xml`       | Diagonal ridges between the wedges                              |
| `creatures/enemies.xml`     | Per-biome enemy `DynamicSpawner`s                               |
| `creatures/bosses.xml`      | The four bosses (`name="boss"` = final, feeds `BossBar`)        |
| `ai/npcs.xml`               | Quest NPC entities (`name=`, `dialogue-id`)                     |
| `atmosphere/ambient-fx.xml` | Ambient particles per biome                                     |

`<biome>` ∈ `forest` (N) · `desert` (E) · `swamp` (S) · `peaks` (O).

## Relevo — `scripts/sculpt_terrain.py`

O heightmap do Terrain3D é plano onde interessa: **32→45 m em ±160**, e os
"Picos Gelados" a oeste até _desciam_ (36→28 m). Sem verticalidade nenhum
prop salva o mapa. O script relê `public/assets/terrain/heightmap.base.png`
(cópia intocada, nunca escrita) e reescreve `heightmap.png`:

| Zona                 | Antes | Depois                |
| -------------------- | ----- | --------------------- |
| Vale / cidade (r<72) | 36    | 36 (intocado)         |
| Floresta (N)         | 36→45 | 36→63, colinas        |
| Deserto (E)          | 36→45 | 36→57, dunas          |
| Pântano (S)          | 36→34 | 36→23, bacia          |
| Picos (O) corredor   | 36→28 | 36→52                 |
| Picos (O) flancos    | 36→28 | até 133               |
| Diagonais            | ~38   | 90 @160, 165 @300     |
| Borda do mundo       | vário | 200 (fecha horizonte) |

```bash
python3 scripts/sculpt_terrain.py            # reescreve heightmap.png
python3 scripts/sculpt_terrain.py --report   # só os perfis, não escreve
python3 scripts/sculpt_terrain.py --restore  # repõe o original
```

**`FLAT_ZONES` no script espelha as coordenadas dos landmarks.** Ao mover um
landmark no XML, mover lá o patamar também — senão fica numa encosta. O
corredor `|z| ≲ 16` a oeste é o caminho do portão até à arena do ogro e não
pode ser fechado.

## Densidade — spawner instanciado vs entidade

`<StaticSpawner>` força `instanced="true"` em todos os `<GLTFLoader>` dos seus
templates (`spawner/parser.ts`): **um draw call por URL**, com LOD na mesma
pool. Por isso os spawners "scenery" (só visual) andam nas centenas.

| Camada                                            | Custo por instância                | Ordem de grandeza |
| ------------------------------------------------- | ---------------------------------- | ----------------- |
| Scenery (`<GLTFLoader>` só)                       | slot na pool instanciada           | 100–250           |
| Colhível (`collider` + `script` + `ResourceNode`) | collider Rapier + script + NavMesh | 12–25             |
| Inimigo (`<DynamicSpawner>`)                      | script + IA + agente NavMesh       | 3–6 por spawner   |

Subir contagens de colhíveis/inimigos custa; subir scenery quase não custa.
`variation="tree|foliage|rock"` só tem efeito no caminho instanciado.

## Richness layers

| Layer         | File                               | What to put there                                 |
| ------------- | ---------------------------------- | ------------------------------------------------- |
| Ground carpet | `vegetation/<biome>.xml`           | `<Vegetation>` density, plant/flower ratios       |
| Canopy/rocks  | `vegetation/<biome>.xml`           | `<StaticSpawner>` scenery + colhíveis             |
| Destinations  | `landmarks/<biome>.xml`            | POIs, ruins, lakes, boss stages, `SpawnExclusion` |
| Routes        | `paths/network.xml` + `trails.xml` | cobble network + spur trails                      |
| Skyline       | `frontier/ridges.xml`              | Diagonal ridge dressing                           |
| Ambient FX    | `atmosphere/ambient-fx.xml`        | Particles (no new lights)                         |
| Biome mood    | `environment.xml`                  | `BiomeRegion` fog/ambient/`clouds`/`rain`         |

**Learnings (enrichment)**

- Um bioma por mesh: `pine_dark`=floresta, `tree_pine`=picos, `dead_willow`=pântano,
  `cactus`=deserto, `tree_oak`=vale. Antes três cunhas partilhavam `tree_pine`/
  `tree_oak`/`dead_bush` e liam-se todas como o mesmo sítio — repara que os
  colliders (`pine_dark_stump_collision`, `dead_willow_stump_collision`,
  `cactus_stump_collision`) já apontavam para o pareamento certo.
- `dust` ≠ ambiente: usar `ground-dust` (lençol) para o deserto; `dust` é FX de quebra.
- **Nevoeiro tem de acompanhar o `far` da câmera.** Com `far=130` + `fog-density=0.024`
  não havia horizonte para ver. Hoje: `far=420`, global `0.008`, `fog-height=96`
  (acima das cristas), cunhas 0.006–0.016.
- Cunhas de `BiomeRegion` até ±520 (eram ±240): a serra da borda tem de ter tint/fog.
- `snow-height` é fração de `max-height` (200 m). A 0.82 = 164 m nenhum pico chegava lá;
  a 0.50 = 100 m as cristas ganham neve.
- **`slope-threshold` é o gate do triplanar, não só da cor de rocha.** O shader
  compara com `normal.y`: a 0.52 o triplanar só entrava acima de ~47° e as
  encostas de 35–43° saíam com a textura esticada em riscas verticais. A 0.78
  o blend começa aos ~28°.
- **`<ParticleSystem transform="pos: x y z">` é posição de MUNDO.** Com o solo
  aos 23–63 m, os `pos: … 0.25 …` do ambient-fx estavam ~36 m enterrados e
  nenhuma partícula se via. Envolver em `<GameObject place="at: x z">`.
- `snow`/`rain` em ciclo precisam de `emission-rate` explícito (~10/s); o
  default de 50/s tapa a paisagem.
- Depois de editar layout/clutter: `vibegame analyze examples/simple-rpg/index.html`
  → `errors=0`.

## Assets em falta (bundle `simple-rpg-assets-v3`)

Estes GLB são referenciados pelo pipeline mas o bundle só traz a colisão —
não deixar `url=` a apontar para eles, o analyze falha:

| Asset                | Estado                             | Substituto em uso                                                 |
| -------------------- | ---------------------------------- | ----------------------------------------------------------------- |
| `form_arch_3`        | GLB completo, não ligado           | desert §1 usa `sandstone_arch` (dedicado); form_arch_3 fica livre |
| `form_cliff_1/20`    | só `_collision.glb`                | relevo do terreno + `moss_rock`                                   |
| `form_outcrop_2/5/8` | só `_collision.glb`                | `rock_mossy` / `moss_rock` (com escala)                           |
| `form_stack_6/11`    | só `_collision.glb`                | `stone_pillar`                                                    |
| `shade_*`            | não existe (só `enemies/shade.ts`) | `bogling` a 1.4×                                                  |

Regenerar: as `form_*` vêm do `rocks3d formation` (ver `Rocks3D/README.md`);
`shade` vem do pipeline `gameassets batch`. Depois de regenerar, repor os URLs.

## City planning (Discordia) — radii

Keep settlement **contained**; let biomes start after a short transition ring.

```
walls ±32  →  SpawnExclusion r=42  →  TerrainPad ~96×96 (falloff 16)
         →  valley ring ±36–58 (spawn/ring.xml)
         →  deep biome spawners |x| or |z| ≥ 58
```

| Contract                         | Value / file                                     |
| -------------------------------- | ------------------------------------------------ |
| Wall half-extent                 | ±32 (`walls.xml`, gates = `RESPAWN_POINTS`)      |
| `SpawnExclusion`                 | `at="0 0" radius="42"` in `cities/discordia.xml` |
| `villageZones` (main.ts)         | `[[0, 0, 42]]` — must match exclusion            |
| `TerrainPad`                     | `size="96 96" falloff="16" corner-radius="14"`   |
| Peri-urban props                 | `cities/discordia/skirts.xml` (`city.skirts`)    |
| Valley vegetation / oaks / rocks | `spawn/ring.xml` region ±58                      |
| Fog / biome atmosphere           | polygons from ~±28 in `environment.xml` (feel)   |
| Deep biome props / enemies       | regions start at ±58 (avoid city sprawl)         |

**Learnings**

- Soft pad (large falloff) > hard plateau — city blends into valley.
- Carpet with `avoid-overlaps="0"` still respects exclusion discs.
- Spawn after pad/lake/river/road flatten (`isGroundMutationPending`) or trees float/sink.
- Gate skirts (west **and** east): pad falloff ∩ flatten road get density boost — engine samples spawn Y with `meshSurfaceResolutionForPoint` so props match the fine mesh (not the coarse ~31 m lattice). See terrain/spawner `context.md`.
- Lake NW of walls must sit **east** of the west river carve (not over the ravine).
- **Enemies: `<Creature>` CCT** grounds Y on terrain heightfield; NavMesh steers `desiredVel` (no Transform XZ write). Spawn seeds Y once. `goblin_collision.glb` unused (capsule).

## Estradas (`paths/network.xml` + `trails.xml`)

Artéria cobble = um `<RoadNetwork>` (~2 m) em `network.xml`:
cruz praça → `mid_*` (muralha ±28) → portões/biomas, **mais anel periurbano**
(`mid_n`↔`ring_ne`↔`mid_e`↔…↔`mid_n`) para não ficar só um `+`.
Gaps rio: `s_bank`↛`s_resume`, `w_bank`↛`w_resume`.
Ramos de terra/areia = `<Road flatten="0">` em `trails.xml` (sem carve; docks nos `via=`).
Ver [`VibeGame/src/plugins/road/context.md`](../../../src/plugins/road/context.md).

## POIs com recompensa (`src/scripts/poi/`)

`createMysticObject` (`src/game/mystic.ts`) fecha o estado "já lido" em
variáveis de **módulo** — cada POI precisa do seu próprio ficheiro, senão dois
POIs partilham o mesmo "consumido". Hoje: `watch-tome` (XP, floresta),
`sun-relic` (ouro, deserto), `bog-idol` (cura, pântano), `frost-cairn` (XP,
picos). O `script=` resolve por `import.meta.glob('./scripts/**/*.ts')`.

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

District detail: [`cities/discordia/context.md`](cities/discordia/context.md).
Engine: vegetation / spawner / terrain (`TerrainPad`) under `VibeGame/src/plugins/*/context.md`.
