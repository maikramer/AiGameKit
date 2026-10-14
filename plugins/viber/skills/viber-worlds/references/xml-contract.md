# Contrato XML do Viber (resumo)

Raiz: `<world>` (ou `<scene>`), attrs `clear-color` e **`static="true"`** (mundo
estático do `viber bake`: poses do XML são FINAIS — o runtime desliga seating/
`place`/geradores; `<StaticSpawner>`, `place` e `<Vegetation>` sem `instances`
são warnings do `analyze`). Tag case-insensitive.
Fonte canónica: `Viber/AGENTS.md` §CONTRATO XML + `Viber/src/recipes/mod.rs` (`KNOWN_TAGS`).

## Atributos universais (qualquer entidade)

| Attr | Notas |
|---|---|
| `name`, `tag` | identificação/seleção; `tag="always-active"` ignora o LOD de IA |
| `script` | Luau em `<mundo>/scripts/` |
| `translation` | "x y z" |
| `euler` | graus XYZ |
| `rotation` | quat "x y z w" (ganha sobre `euler`; **3 valores = euler em radianos**) |
| `scale` | "x y z" |
| `collider` | `none`/`auto`/box/sphere/cylinder/capsule/mesh/precompute — `auto` numa primitiva = colisor exato; num GLTF = caixa das bounds da cena inteira |
| `rigidbody`/`body` | kinematic/dynamic/… (Rapier) |
| `destructible` | colheita nativa (ver abaixo) |
| `place`, `seat` | em `<Composition>` — colocação explícita / `seat="none"` flutuante |

Primitivas aceitam material: `base-color`, `metallic`, `roughness`, `opacity`
(0=invisível, <1=Blend), `emissive`, `texture`/`texture-url`, `texture-tile-size`,
`normal-map`/`normal-map-url` (linear — sem ele uma parede texturada fica lisa).

## Tags

### Estrutura
- `Entity`/`Group` — contentor transform-only (hierarquia via filhos)
- `<Include src="…">` — composição, depth ≤ 8, ciclos fail-fast
- `Composition` — objeto inteiro em primitivas: 1 corpo + colisor exato por parte;
  attrs `place` (`"at: x z; align-to-terrain: …; base-y-offset: …"`), `seat="none"`;
  partes `Box`/`Cuboid`/`Sphere`/`Cylinder`/`Plane`/`Capsule` + sub-pivots `Group`.
  Guia: `Viber/docs/COMPOSITION.md`
- `Prototype`/`Use` — definir 1× (`<Prototype id="…">`, não spawna) e instanciar N
  (`<Use prototype="…" …>` deep-clone com overrides no root)

### Formas
- `Cuboid` `half-size` (vec3) · `Sphere` `radius` · `Cylinder` `radius`+`half-height`
  · `Plane` `half-size` (vec2, XZ) · `Capsule` `radius`+`half-height`

### Luzes e câmara
- `PointLight` `color`, `intensity` (1200 lm), `radius`, `shadows` (orçamento de 12
  cube shadow maps mais próximos; teto 60 m; gate de dia)
- `DirectionalLight` `color`, `illuminance` (lux), `direction` ("x y z"), `shadows`
- `AmbientLight` `color`, `brightness`
- `OrbitCamera` `target`, `distance`, `height`, `pitch` · `ThirdPersonCamera`
  (interactive; defaults target=player, distance 4, height 1.6)
- `PlayerGLTF` `model-url` (obrigatório), `name` (player), `pos`, `speed`
- `GltfScene` `url` (obrigatório; `/assets/...` contra asset roots; meshopt
  expandido à leitura; Draco/Basis NÃO suportados)

### Spawn
- `StaticSpawner` — `count`/`seed`, ou `density-per-km2`+`max-instances`;
  `region-min`/`region-max`, `cluster-count`/`cluster-radius`,
  `footprint-radius`+`avoid-overlaps` (0=AUTO: meia-largura XZ do AABB),
  `max-slope-deg`, `max-slope-attempts` (32), `avoid-water`, `in-water` (à lâmina),
  `near-water`+`near-water-radius`, `avoid-road` (default ON), `avoid-cliff`
  (default ON)+`cliff-margin` (2 m), `activation-radius`, `align-to-terrain`,
  `base-y-offset`, `scale-min/max`+`scale-axis-min/max`, `random-yaw`, `max-distance`.
  Template = primeiro glTF filho. Colocação determinística (SplitMix64 por seed);
  registo de ocupação XZ partilhado com folga 0.6 m; rejeições contadas por motivo
  no log (`viber debug logs`). Altura = superfície desenhada.
- `DynamicSpawner` — mesmos attrs; template `Creature`→glTF. Sem `script` = IA da
  engine (wander/chase+respawn); com `script` = Luau. Sempre verticais.
- `SpawnExclusion` — `at` ("x z"), `radius`; honrada por TODOS os spawners
- `Vegetation` — `meshes` (lista), `density-per-km2`, `max-instances` (800/tag),
  `max-distance`, `cluster-*`; `avoid-road`/`avoid-cliff` default ON;
  **`instances`** = `.vinst` pré-gerado pelo bake (ex.
  `instances="bake/veg/NNN.vinst"`) — o runtime carrega as instâncias e não
  calcula nada; os outros attrs ficam como receita
- `Scatter` — **saída do bake**, NO-OP em runtime: a RECEITA de um gerador
  (attrs de colocação do `StaticSpawner` + `id`). Cada spawner cozido vira
  `<Scatter id=…>` + `<Group name="spawn/…" generated-by="id">` com as
  instâncias (editável). Regenerar sobre o terreno atual:
  `viber tool scatter id=…` (offline ou `--live`, transação desfazível)

### Colheita (attr `destructible`, component-string)
`preset` (burst), `hits` (3), `hit-preset`, `shake-on-hit`, `crack-on-hit`,
`break-style` (`burst`|`fall`|`shatter`), `range` (3.5). Filho
`<ResourceNode kind="wood|stone" yield="N"/>` define loot. [J]/clique perto do prop:
herói equipa picareta/machado; `fall` = árvore tomba e fica toco; loot → vault+XP+quests.

### Terreno (100% volumétrico — transvoxel sobre campo voxel)
- `Terrain` — `heightmap` (PNG/.ahgt; ausente = procedural por `seed`),
  `world-size` (256), `max-height` (50), `chunk-size` (64), `resolution` (64),
  `levels` (3), `lod-distance-ratio`, `render-distance`, `height-smoothing`,
  `collision-resolution` (64; **0 desliga colliders**), `cliff-angle` (50°),
  `sharpen`, `seed`, tint legado (`base-color`…`slope-softness`) OU blend de
  camadas: `layers` (≤13 aliases do pool: `grass vale_grass dirt dirt_trail
  forest_floor gravel mountain_stone sand desert_sand snow_peak swamp_mud dirt_road
  pebbles`), `shore-width` (5)
- `TerrainPad` — `at` ("x z"), `size` ("w d"), `falloff` (8), `corner-radius`,
  `height` (ausente = auto)
- `Lake` — `at`, `radius`, `depth`, `water-offset`, `color`, `opacity`, `ripple`,
  `bank` (soft|beach|cliff|terraced|gorge|overhang), `rocks`+`rocks-density`+
  `rocks-scale-max`, `seed`/`stretch`/`axis`/`lobes` (autoria da forma), filhos
  `<Island at radius height/>`
- `River` — `path` ("x z x z …" ≥2 pontos), `width`, `depth`, `water-offset`,
  `bank-width`, `bank-height`, `pool-spacing`, `cascades`, `waterfalls`+
  `waterfall-min-drop`+`waterfall-notch`, `spring`, `bank`/`rocks` idem Lake.
  Rio × cliff = cachoeira automática; confluências sobem à cota do lago
- `Cut` — `path`, `width` (8), `depth` (4), `wall` (vertical|concave|convex|
  columnar|terraced|overhang), `seed`. Trincheira/cânion seco
- `Plateau` — `at`, `size`, `height` (6), `falloff`, `corner-radius`, `wall`, `seed`
- `Cliff` — `path` (linha de creste), `width`, `height` (auto = diferença natural),
  `angle`, `profile` (vertical|concave|convex|columnar|terraced|overhang|arch),
  `side` (auto/left/right), `noise`, `gullies`, `notches`, `talus`, `seed`.
  Sólido 3D no voxel — NÃO carva; manter afastado de estradas (~30 m)
- `Cave` — `path`, `radius` (valor ou perfil "2.5 5 3"), `depth` (8), `open-ends`,
  `mouth-flare`, `mouth-fraction`; filhos `<Chamber at radius height/>`,
  `<Shaft at radius/>`
- `Arch` — `at` OU `path`, `width`/`span`, `height`, `thickness`, `depth`, `yaw`,
  `spans`, `profile` (portal|natural)
- `Bridge` — `path`, `width`, `rise`, `thickness` (≥2 m), `style` (stone|natural),
  `spans`, `pier-width`, `parapet`, `clearance`. Sólido voxel — o herói anda por
  cima. Travessias curtas de estrada: preferir `<Composition>`
- `RockFeatures` — `region` ("minX minZ maxX maxZ"), `seed`, `arches`/`caves`/
  `bridges`, `min-slope`/`max-slope`, `min-drop`, `spacing`, `clear-of-roads`,
  `min-rise`. Semeia features e resolve em specs normais
- `Road` — `path` (obrigatório), `width` (2), `profile` (artery), `flatten` (true),
  `flatten-falloff`/`flatten-window`/`flatten-max-grade`, `texture-url`,
  `texture-scale`, `edge-feather`
- `RoadNetwork` — `default-profile`/`default-width` + filhos `Way id xz [width]` e
  `Segment a b [via] [width] [profile]` (`profile="bridge"` = ribbon sem collider)
- `GroundDecal` — `at`, `radius`/`size`/`half-size`, `feather` (2.5), `noise`,
  `texture-url`, `texture-scale` (9), `base-color`, `lift`. Puramente visual
- `InteriorScene` — `at` (CENTRO), `size`, `room-size`+`room-origin`,
  `camera-distance`/`camera-pitch`/`camera-yaw`. Bolsa FORA da pegada do
  heightmap; um só por mundo. Ex.: `examples/simple-rpg/world/interiors.xml`

**Ordem de carve:** Pads → Lakes → Rivers → Roads (pontes por último) → decals.
Cliffs/Caves/Archs/Bridges são sólidos voxel construídos depois do carve.

### Mundo vivo
- `Sky` — domo procedural WGSL; `model` (analytic|nishita), consts especializadas
  por mundo
- `DayCycle` — `minute-of-day`, `minutes-per-real-second`, `dawn-minute`,
  `dusk-minute`, `ambient-day/night-intensity`, `max-sun-elevation`
- `Weather` — `wind` (vec2), `wind-strength`, `clouds`, `rain` (0..1), `cycle`
  (scheduler determinístico a 240 s)
- `BiomeRegion` — `id`, `polygon` ("[x,z;x,z;…]"), `display-name`, `fog-density`,
  `tint`, `pp-exposure`, `pp-bloom-strength`, `rain-scale`
- `WorldBorder` — `radius` (3800), `warn-seconds`, `margin`
- `AudioMixer` (`master`/`music`/`sfx`) · `MusicLayer` (`layer`, `sound` `bgm-x`,
  `base-volume`)

### EngineConfig (consumidores em runtime)
- `NavMesh` (`agent-radius`, tile, custo fora-de-estrada)
- `SpawnGate` (`target-entity`, `y-fallback`) — segura o player até o terreno ter
  collider
- `ProjectileTemplate` (`id`, `speed`, `damage`, `faction`, `gravity`…) — disparado
  por `viber.fire_projectile(id)`
- `AdaptiveQuality` (`target-fps` 55, `mode`)
- `PostFxDebugToggle` (`bindings` ctrl+1:bloom, …)

### Gameplay/HUD
- `DialogueNPC` — `dialogue-id` (obrigatório); [E] abre diálogo real das quests JSON
- HUD: `HudScreenLayer`, `HealthBar`, `XpBar`, `TargetBar`, `Minimap` (`range`),
  `Compass`, `InteractionPrompt`, `DialogueBalloon`, `TabbedModal`, `QuestTracker`,
  `WaypointArrow` (IR genérica; UI real em `src/hud/`)
- `ResourceChip` — `resource` (gold/wood/stone), `icon`, `target-entity`
- `Landmark`/`SpawnPoint` — marcos/spawnpoints com fallback das tabelas antigas
- `UiStyle`/`UiRoot` — UI declarativa (ver SKILL.md)

## Regras de valores

- Vetores "x y z": broadcast de 1 valor OK; **2 valores = erro**.
- Bools tolerantes: bare (`<PointLight shadows>`), `true/1/yes/on`, `false/0/no/off`.
- Números NaN/inf rejeitados. Includes podem sair da árvore (`..`, symlinks).
- Quests JSON em `<mundo>/quests/`: `id`/`npc`/`biome`/`title`/`lines_*`/
  `objective{type: kill|collect|visit, target, count, radius}`/`rewards`.
  Um `biome` em falta derruba o FICHEIRO inteiro (warn+skip).
- Save: `~/.local/share/viber/<mundo>.save.json` (`save.dir` sobrepõe).
- RNG determinístico ÚNICO (SplitMix64): "mesma seed, mesmo mundo" — spawner/
  terreno/IA/clima partilham a sequência.
