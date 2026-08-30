# public/world — mapas modulares

Scene fragments loaded via `<Include src="/world/…">` from `index.html`.
**Agents: edit the domain file below, not the whole `index.html`.**

| Path                        | Contents                                                        |
| --------------------------- | --------------------------------------------------------------- |
| `environment.xml`           | Sky, lights, post, audio, weather, `BiomeRegion`, `<DayCycle>` (dia/noite) |
| `cities/discordia.xml`      | City shell (`SpawnExclusion` + `TerrainPad` + Includes)         |
| `cities/discordia/*.xml`    | Districts: `houses`, `utilities`, `walls`, `roads`, `skirts`, … |
| `cities/town-demo.xml`      | Demo town @ (420,420) — `CityGrid` + prefabs                    |
| `spawn/ring.xml`            | Valley resource ring 52–116, river, bridges, peri-urban carpet  |
| `spawn/dressing.xml`        | Cairns `form_stack` no anel do vale (fora da cidade)            |
| `paths/network.xml`         | Cobble `<RoadNetwork>` cruz + anel periurbano + 4 biomes (~2 m) |
| `paths/trails.xml`          | Dirt/sand spur `<Road flatten="0">` to landmarks                |
| `paths/lanterns.xml`        | Aleia de tochas (poste + chama + PointLight) nas 4 artérias      |
| `vegetation/<biome>.xml`    | Carpet + canopy + rocks, one file per cardinal wedge            |
| `landmarks/<biome>.xml`     | Destinations: outposts, ruins, mine, arena, boss glades         |
| `landmarks/wilds.xml`       | Anel selvagem (8 km): Ruína de Orm (leste, guardas bandit), Clareira Paradisíaca (oeste, lagoa turquesa + cura), Mirante da Caldeira (sul, cota 133 m) — POIs em `scripts/poi/`, trilhas em `paths/trails.xml` |
| `frontier/ridges.xml`       | Diagonal ridges between the wedges                              |
| `creatures/enemies.xml`     | Per-biome enemy `DynamicSpawner`s                               |
| `creatures/bosses.xml`      | The four bosses (`name="boss"` = final, feeds `BossBar`)        |
| `ai/npcs.xml`               | Quest NPC entities (`name=`, `dialogue-id`)                     |
| `atmosphere/ambient-fx.xml` | Ambient particles per biome                                     |

`<biome>` ∈ `forest` (N) · `desert` (E) · `swamp` (O) · `peaks` (S).
Pós-Terrain3D as bacias baixas reais ficaram a Oeste (pântano) e as encostas
íngremes a Sul (picos) — sync com `LOOKOUT_GATES` (`src/game/city-amenities.ts`).

## Relevo — 100% Terrain3D (mundo dobrado 8 km, 2026-08-29)

**Mapa dobrado:** world-size 4000 → 8000 m (heightmap 2048 → 4096, mesma
densidade de 15,4×). A difusão **não reproduz** a geografia antiga a outra
resolução (correlação 0,26 entre o mapa 2048 e a janela correspondente do
4096) — o mundo novo é uma geografia nova: uma **caldeira** com piso a ~24 m
e muros a +50 m, centrada na origem via `<Terrain pos="-51 0 51">`. Toda a
infraestrutura auto-esculpe-se no novo vale (pads, estradas, rio, lagos,
pontes re-assentam sozinhas — verificado). A escolha do vale foi por scan
(planície r=250 m, muro no anel 600–1400 m). Seeds descartadas: 20260818
(planalto std 0,12), 20260819 (0,23), 20260821 (0,24). Anel selvagem
~400–3800 m sem conteúdo — é a tela para os novos ambientes. Cunhas de bioma
estendidas para ±4040; WorldBorder 3800; levels 6.

O relevo é o output cru do Terrain3D, sem pós-processamento. O antigo
`scripts/sculpt_terrain.py` (cunhas cardeais + anel de borda + patamares nos
landmarks) foi removido: escrevia uma fórmula analítica por cima dos 4 km
todos e só ~3 % do mapa (a zona jogável ±400 m) justificava os parâmetros —
o resto ficava com cones radiais e costuras a 45°.

```bash
cd public/assets/terrain
terrain3d generate \
  --prompt "vale fluvial de fantasia RPG — fundo de vale habitável, rio, encostas e montanhas em redor" \
  --seed 20260820 --size 4096 --offset-i 774 --offset-j 2866 \
  --world-size 8000 --max-height 200 \
  --mode continental --format ahgt \
  --output terrain.ahgt --metadata terrain.json
```

`--offset-i/-j` panoram o mundo infinito do mesmo seed. Esta janela foi
escolhida por scan: a zona jogável (±400 m) cai num sistema de drenagem
dendrítico — vale habitável ao centro, montanha a sul, descida aberta a
oeste, ravina a leste. 65 m de relevo com 92 % abaixo dos 25° de declive.

`--size` controla o rácio de escala horizontal (`size × 30 m ÷ world_size`);
o próprio Terrain3D avisa acima de 32× (declives artificiais). 2048 sobre
4000 m dá 15,4× e um `.ahgt` de 7,3 MB — 4096 dava 30,7× e 28,5 MB.

`world-size` / `max-height` do `terrain.json` têm de bater com os atributos do
`<Terrain>` no `index.html`. O formato `.ahgt` (uint16 + deflate) é lido
nativamente pelo plugin `terrain` — sem o terracing de 0,78 m do PNG 8-bit.

## Chão e materiais — passagem de embelezamento (2026-08-29)

O relevo estava bom mas a **superfície** não: o mapa lia-se lavado, com o chão
a sobrepor-se ao resto. Quatro causas, todas corrigidas:

1. **Texturas de bioma erradas para o sítio.** `forest_floor` era relva com
   folhas de outono vermelhas — numa "Floresta Sombria" lia-se como confetti;
   `desert_sand` saíra laranja-néon; `snow_peak` era cascalho cinzento-azulado.
   Regeneradas com `regen_textures.py` (prompts novos lá documentados) e
   pós-processadas de forma determinística (`POST` no mesmo ficheiro): realce de
   contraste **local** na areia (o difuso vinha com `std ≈ 7`, um lençol liso),
   escurecimento do `swamp_mud` (lia-se como praia) e reancoragem quente do
   `cobblestone_road` (cinzento-neutro + IBL do céu = praça "de gelo").

2. **`base-color` do `<Terrain>` era verde (`#8cb866`).** A cor do material
   multiplica **todas** as camadas de bioma — a areia e a neve saíam
   esverdeadas. Hoje é neutro (`#c9c5ba`) e a cor vem das texturas; o gradiente
   de altitude (`color-low/mid/high/rock`) desceu para
   `height-blend-strength="0.22"` (era 0.40) para tingir sem invadir.

3. **`normal-strength="2.6"` no terreno.** Com o IBL do céu e `roughness 0.82`
   o normal map criava um brilho especular por píxel — o chão ficava com um
   véu de "geada" em todos os biomas. Hoje: `normal-strength 1.15`,
   `roughness 0.95`, `ao-strength 0.85`.

4. **`pp-exposure` das `BiomeRegion` SUBSTITUI a global, não multiplica**
   (`biomes/systems.ts`: `Postprocessing.toneMappingExposure = lerp(pick(...))`).
   As cunhas estavam em 0.8–1.12 com a global em 0.88 — ou seja, o deserto
   corria mais claro que a baseline. Hoje a global é 0.76 e as cunhas
   0.70–0.78, sempre **abaixo** dela. Sol de 5.2 → 2.6, `environment-intensity`
   0.3 → 0.38, `contrast` 0.08 → 0.18, `fog-sun-influence` 1.0 → 0.45 e
   `fog-sky-haze` 0.22 → 0.10 (o horizonte era uma banda branca).

**Albedo das rochas.** `form_stack_6/11` e `stone_cairn` vieram do paint com
albedo lavanda/azul/laranja — no mapa liam-se como gelatinas pastel. Os GLBs do
pool **não são versionados** (`shared-assets/.gitignore` ignora
`public/assets/meshes/`), por isso o repaint é um passo re-executável:

```bash
cd examples/simple-rpg
npm run fetch-assets          # se o pool ainda não estiver local
python3 scripts/repaint_rock_albedo.py    # precisa do `ktx` no PATH
```

Rampa de luminância → pedra neutra, re-encode ETC1S e repack do GLB (offsets do
`EXT_meshopt_compression` recalculados). Um sidecar `<glb>.repainted.json` evita
escurecer duas vezes; `--force` ignora-o.

**Vestir o vale.** `spawn/dressing.xml` deixou de ser 88 cairns iguais: cada
spawner sorteia entre quatro templates (dois cairns + `rock_boulder` +
`rock_mossy`) e as diagonais ganharam quatro prados de `<Vegetation>` com
`flower-density-ratio="0.55"`. `paths/lanterns.xml` põe 16 tochas a ladear as
quatro artérias (pares a 4.2 m da centreline, `align-to-terrain: 0`) — de dia
marcam o caminho, de noite são o que se vê. O motor só acende as 12 PointLights
mais próximas (`MAX_POINT_LIGHTS`), por isso a aleia inteira não pesa.

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

| Asset                | Estado                             | Substituto em uso                                            |
| -------------------- | ---------------------------------- | ------------------------------------------------------------ |
| `form_arch_3`        | GLB completo, ligado               | desert landmark + spawner no deserto                         |
| `form_cliff_1/20`    | GLB completo, ligado nas cristas   | `frontier/ridges.xml`                                        |
| `form_outcrop_2/5/8` | GLB completo, ligado nas cristas   | `frontier/ridges.xml`                                        |
| `form_stack_6/11`    | GLB completo, ligado               | cidade, vale (`spawn/dressing.xml`), cristas, pântano, picos |
| `shade_*`            | não existe (só `enemies/shade.ts`) | `bogling` a 1.4×                                             |

Regenerar: as `form_*` vêm do `rocks3d formation` (ver `Rocks3D/README.md`);
`shade` vem do pipeline `gameassets batch`. Depois de regenerar, repor os URLs.

## City planning (Discordia) — radii

Keep settlement **contained**; let biomes start after a short transition ring.

```
walls ±39.8  →  SpawnExclusion r=52  →  TerrainPad 120×120 (falloff 20)
           →  valley ring 52–116 (spawn/ring.xml)
           →  deep biome spawners |x| or |z| ≥ 116 (a jusante do rio)
```

| Contract                         | Value / file                                      |
| -------------------------------- | ------------------------------------------------- |
| Wall half-extent                 | ±39.8 (`walls.xml`, gates = `RESPAWN_POINTS`)     |
| Ruas internas                    | anel ±25, docks `mid_*` ±32 (`paths/network.xml`) |
| `SpawnExclusion`                 | `at="0 0" radius="52"` in `cities/discordia.xml`  |
| `villageZones` (main.ts)         | `[[0, 0, 52]]` — must match exclusion             |
| `RESPAWN_POINTS` (main.ts)       | praça + ±50 nos quatro portões                    |
| `TerrainPad`                     | `size="120 120" falloff="20" corner-radius="18"`  |
| Peri-urban props                 | `cities/discordia/skirts.xml` (`city.skirts`)     |
| Valley vegetation / oaks / rocks | `spawn/ring.xml` region ±116                      |
| Fog / biome atmosphere           | polygons from ~±28 in `environment.xml` (feel)    |
| Deep biome props / enemies       | wedges start at ±116 (para lá do rio)             |

**Compactação 2026-07-30.** Muralha ±64 → ±39.8 (−38 % de lado, −61 % de área).
O que encolheu foi o _vazio_ entre distritos, não as ruas: a cruz praça→portões
e o anel periurbano continuam lá, agora a ±25/±32. Um passo de muralha é fixo
(`city_wall_seg_*` = 6.615 m), por isso o semi-lado não é livre — sai de
`5 segmentos × 6.68 + arco 10.146 + canto 2.565`. Regenerar com
`python3 scripts/gen_city_walls.py > public/world/cities/discordia/walls.xml`.
A `SpawnExclusion` caiu 84 → 52, libertando a coroa 52–84 que era planalto
pelado; as contagens do `spawn/ring.xml` subiram ~40 % para a preencher.
As cunhas de bioma **não** se mexeram: começam onde o rio manda (±116), não
onde a cidade acaba.

**Densidade (pós câmera/bioma alargados):** spawners `count=` ×2 (vegetação,
anel, cristas, landmarks, inimigos). Tapete `<Vegetation>` erva/planta/flor
reativado com tapetes `<Vegetation>` a ~10000 instâncias/bioma (alinhados ao cap do spawner) — ver `vegetation/context.md`.

**Learnings**

- Soft pad (large falloff) > hard plateau — city blends into valley.
- Carpet with `avoid-overlaps="0"` still respects exclusion discs.
- Spawn after pad/lake/river/road flatten (`isGroundMutationPending`) or trees float/sink.
- Gate skirts (west **and** east): pad falloff ∩ flatten road get density boost — engine samples spawn Y with `meshSurfaceResolutionForPoint` so props match the fine mesh (not the coarse ~31 m lattice). See terrain/spawner `context.md`.
- Lake NW of walls must sit **east** of the west river carve (not over the ravine).
- **Enemies: `<Creature>` CCT** grounds Y on terrain heightfield; NavMesh steers `desiredVel` (no Transform XZ write). Spawn seeds Y once. `goblin_collision.glb` unused (capsule).

## Estradas (`paths/network.xml` + `trails.xml`)

Artéria cobble = um `<RoadNetwork>` (~2 m) em `network.xml`:
cruz praça → `mid_*` (±32, 8 m dentro da muralha) → portões/biomas, **mais anel periurbano** (±25)
(`mid_n`↔`ring_ne`↔`mid_e`↔…↔`mid_n`) para não ficar só um `+`.
**Duas travessias em ponte** (as únicas — o `analyze` barra estrada em água sem
`profile="bridge"`): o rio norte na artéria da floresta (z≈214) e a **Lagoa
Grande do pântano** na artéria oeste (`w_lake_e`↔`w_lake_w`, lagoa -190,-16
r=24; tochas nas cabeças + acampamento de pescador na margem norte em
`landmarks/swamp.xml`). Trilhas de terra/areia = `<Road flatten="0">` em
`trails.xml` (sem carve; docks nos `via=`; o ramo dos menires sai do pad
`n_resume`, a trilha da margem do pântano contorna a lagoa por NE).
Gaps rio: `s_bank`↛`s_resume`, `w_bank`↛`w_resume`.
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

- `name="player"` / `name="boss"` / `name="merchant"`
- `SpawnExclusion at="0 0" radius="52"` in `cities/discordia.xml`
- Cardinal gates at wall ±39.8 (`RESPAWN_POINTS` = ±50)
- Quest `dialogue-id` matches JSON under `src/data/quests/`

## Dia/noite (`<DayCycle>`)

O `DayCyclePlugin` (main.ts) + `<DayCycle>` no `environment.xml` comandam o
`<Sky>` procedural e a luz ambiente: dia de 20 min reais, amanhecer 05:30,
anoitecer 19:30, nadir do sol a −22° (noite escura — as tochas de
praça/portões/ponte e o acampamento da lagoa assumem; `ambient-night-intensity`
0.07). O relógio aparece no HUD (`<Clock>`), salva globalmente e o write direto
de `GameClock.minuteOfDay` serve para QA (tour noturno). `sleepUntilMorning`
está disponível para futuros pontos de descanso. God-rays/fog-sun foram
afinados para não lavar a tela com o disco solar abaixo do horizonte.

## Ambiência de água

Loops espaciais `water-flow` / `water-lake` (Text2Sound, `regen_sounds.py` →
`public/assets/audio/sfx/world/`) ligam por proximidade com histerese via
`scripts/ambient-water.ts` — âncoras: ponte do rio norte, Lagoa Grande, lago
do vale, lagoa leste, oásis, lago gelado.

Quest/dialogue **data** stays in `src/data/quests/` and `public/data/ai/*.yaml` — not in these Scene XMLs. City-watch bounties live in `city_quests.json` (`npc: notice_board`, taken via `notice-board.ts`); the blacksmith job is `city_stone` on `npc_blacksmith` in `forge.xml`. Chapel healer is `healer.ts` (gold for a full heal), not a quest. Plaza campfire (`campfire.ts`, [G]) and well (`well.ts`, [F]) are free heals with cooldown; forge anvil (`anvil.ts`, [K]) crafts a bomb; watchtower guard (`watch-guard.ts`, [F]) pins the four gates on the compass.

District detail: [`cities/discordia/context.md`](cities/discordia/context.md).
Engine: vegetation / spawner / terrain (`TerrainPad`) under `VibeGame/src/plugins/*/context.md`.
