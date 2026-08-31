# AGENTS.md — Viber

Engine de jogo NATIVA em Rust/Bevy 0.19 que corre mundos declarativos XML do
AiGameKit — sem browser, sem three.js. Estado: **Fase 0** (parse → IR → spawn).
Nomenclatura segue **Bevy** (`translation`, `euler`, `half-size`, `base-color`),
não Unity/three.js.

## WHERE TO LOOK

| Tarefa | Ficheiro(s) | Notas |
|--------|-------------|-------|
| CLI (`run` / `analyze`) | `src/main.rs` | `analyze` é headless, exit 1 em erro |
| XML: parse, includes, valores | `src/xml/` | `include.rs` (expansão), `values.rs` (parsers tolerantes) |
| IR de entidades + spawn Bevy | `src/recipes/` | `mod.rs` (IR), `spawn.rs`, `transform.rs` (euler→quat) |
| Terreno (specs, sampler, mesh, LOD) | `src/terrain/` | `spec.rs` (contrato), `sampler.rs`/`heightmap.rs` (altura), `mesh.rs` (chunks), `plugin.rs` (LOD runtime), `runtime.rs` (bootstrap + carve) |

## COMANDOS

```bash
cd Viber && cargo run -- analyze <world.xml>   # valida headless (exit 1 em erro)
cd Viber && cargo run -- run <world.xml>       # janela Bevy
cd Viber && cargo test                          # testes headless
make test-viber                                 # atalho monorepo
```

### CLI instalado (`viber`, via instalador unificado)

```bash
./install.sh viber            # raiz do monorepo: cargo build --release + ~/.local/bin/viber
viber create <nome>           # scaffold <nome>/world.xml (falha se a pasta existe)
viber analyze [world.xml]     # valida headless; sem caminho procura world.xml / worlds/*.xml
viber run [world.xml]         # janela Bevy; `--release` e `--no-cargo` disponíveis
viber --version | help
```

`viber run` dentro de um checkout do Viber delega em `cargo run -- run <mundo>`
(parcidade com o `vibegame run`, que reconstrói a engine) — o binário instalado
corre directo fora do checkout. `analyze` nunca delega (CI-ready, mesmo parser
do binário instalado).

### Debug bridge (`viber run --bridge`)

BRP sobre HTTP (`bevy_remote`) na porta **15702** (`--bridge PORT` muda) — o equivalente nativo do tooling Chrome DevTools MCP do
VibeGame. Métodos JSON-RPC: `viber.ping`; `viber.screenshot` +
`viber.screenshot_status` (request/poll — a captura completa em ~1-3 frames);
`viber.tree` (árvore de entidades: id/nome/pai/transform/componentes);
`viber.logs` (ring-buffer de tracing, 1000 entradas); `viber.input.key/text/
click/move` (input sintético: `KeyboardInput`/`MouseButtonInput`/`CursorMoved`
+ `ButtonInput`). Os métodos BRP builtin (`world.query`, `world.spawn_entity`,
`world.insert_components`, `world.mutate_components`, …) também ficam expostos
— inspecção e mutação live do ECS.

Cliente CLI (`--port` ou `VIBER_BRIDGE_PORT`):

```bash
viber run world.xml --bridge &   # engine com bridge
viber debug probe                          # bridge vivo? (como list_pages)
viber debug screenshot -o shot.png         # captura da janela
viber debug tree [--json]                  # entidades (como take_snapshot)
viber debug logs [--limit N] [--json]      # console
viber debug click 400 300 [--button right]
viber debug move 400 300
viber debug key w | space | esc | up | ctrl [--shift]
viber debug text "hello"                   # typing sintético por char
```

Detalhes: handlers correm como sistemas exclusivos em `RemoteLast` (depois de
`Last`); screenshots são request+poll porque a captura precisa de frames de
render (bloquear o handler congelaria a engine); o cliente HTTP é std-only
(`src/bridge/client.rs`, retry no connect pois o bind é assíncrono). Código:
`src/bridge/` (`mod.rs` server, `client.rs` cliente, `logs.rs` layer de
tracing). Testes headless em `src/bridge/tests.rs` (App mínima + bridge real
em loopback).

## CONTRATO XML (Fase 0)

Raiz: `<world>` (ou `<scene>`), attr `clear-color` (`#rgb`/`#rrggbb`/`0x…`/nome).

| Tag | Atributos próprios |
|-----|--------------------|
| `Entity` / `Group` | contentor transform-only (hierarquia via filhos) |
| `Cuboid` | `half-size` (vec3) |
| `Sphere` | `radius` |
| `Cylinder` | `radius`, `half-height` |
| `Plane` | `half-size` (vec2, plano XZ) |
| `Capsule` | `radius`, `half-height` |
| `PointLight` | `color`, `intensity` (default 1200 lm), `radius`, `shadows` |
| `DirectionalLight` | `color`, `illuminance` (lux, default bevy 10 000), `direction` ("x y z", para onde a luz viaja; −Z da entidade alinha à direção), `shadows` |
| `AmbientLight` | `color`, `brightness` — aplicado como recurso `GlobalAmbientLight`, não entidade |
| `OrbitCamera` | `target` (nome de entidade), `distance`, `height`, `pitch` (graus; quando presente sobrepõe `height` via `height = distance·tan(pitch)`) |
| `GltfScene` | `url` (obrigatório; `/assets/...` resolve contra a asset root do mundo — a pasta que contém `assets/`) + attrs universais; cena default do GLB spawna como filhos da entidade (transform aplica); load assíncrono, falha = warn + nó vazio. GLBs do pipeline vêm meshopt-comprimidos (bevy 0.19 não lê EXT_meshopt) → espelho decomprimido via `Viber/scripts/sync_assets.py` |
| `StaticSpawner` | `count`, `seed`, `region-min`/`region-max` ("x y z"), `cluster-count`/`cluster-radius`, `footprint-radius`+`avoid-overlaps`, `max-slope-deg`, `avoid-water`, `align-to-terrain`, `scale-min`/`max`+`scale-axis-min`/`max`, `random-yaw`, `max-distance`; template = primeiro glTF (`GLTFLoader`/`GltfScene`) na subárvore filho. Colocação determinística (SplitMix64 por seed, rejeita água/declive/sobreposição, tenta count×8+64); `profile`/`variation`/`ground-align` aceites sem efeito. Espelha `src/spawner.rs` (função pura `compute_placements`) |
| `ParticleSystem` | `preset` (fire, smoke, fireflies, ground-dust, sparkle, leaves, snow, sand-dust, magic, core; desconhecido = core) + `transform="pos: x y z"` (component-string) + overrides em `particle-emitter="preset: …; emission-rate: …; start-life-min/max: …; start-speed-min/max: …; start-size-min/max: …; start-color: #hex; looping: …; world-space: …"`. Emissor CPU billboard (`src/particles.rs`): mesh de capacidade FIXA por emissor (quads degenerados nos slots livres — realocar por frame tripava use-after-free no slab allocator), vertex color com fade por vida, material unlit Add (fogo/magic/sparkle/fireflies) ou Blend (resto) |
| `PlayerGLTF` | `model-url` (obrigatório), `name` (default `player`), `pos` (alias de `translation` — tags verbatim mantêm `pos`), `speed` futuro. Componente `Player` + WASD/setas relativo ao yaw da câmara (Shift = sprint ×1.8), assenta no terreno via `TerrainRuntime::sample` todos os frames (`src/player.rs`) |
| `ThirdPersonCamera` | alias interactivo do `OrbitCamera` com defaults `target="player"`, `distance` 4, `height` 1.6; aceita `mouse-sensitivity`. Drag (qualquer botão) = yaw/pitch, scroll = zoom (clamp 2–80 m); câmaras extra são rebaixadas a Group com warning |
| `DialogueNPC` | `dialogue-id` (obrigatório; ausente = skip), `marker-height` (2.5), `portrait-url`/`voice-sfx` aceites sem efeito. Componente `DialogueNpc` + marcador esférico dourado emissivo a `marker-height` acima do NPC; interação: player a <3.5 m + E → log no bridge (`src/player.rs::dialogue_interaction`); UI de diálogo na fase HUD |
| `ResourceChip` | `resource` (obrigatório), `icon`, `target-entity` — chip de HUD real: texto UI absoluto (`resource 0`) empilhado no canto superior esquerdo (`src/recipes/spawn.rs`, adiado para o fim do startup); valores/ícones chegam com a fase de economia |
| `DynamicSpawner` | mesmos attrs de colocação do `StaticSpawner` (count/seed/region/avoid-*/random-yaw/max-slope/max-distance); template = `Creature`→glTF. Spawn único (respawn/comportamento chega com scripts) |
| `SpawnExclusion` | `at` ("x z"), `radius` — círculo global; recolhido num recurso e respeitado por TODOS os spawners (rejeita candidatos dentro do raio) |
| `Vegetation` | `meshes` (lista separada por espaços), `density-per-km2`, `seed`, `region-*`, `scale-*`, `max-slope-deg`, `avoid-water`, `max-distance`, `cluster-*`; count = densidade × área km² com cap `max-instances` (default 800/tag — o original GPU-instancia ~100k; instancing é follow-up). `smart`/`wind`/`flower-*`/`plant-*` aceites sem efeito |

Primitivas aceitam material: `base-color`, `metallic`, `roughness`.
Atributos universais: `name`, `tag`, `script`, `translation`, `euler` (graus XYZ),
`rotation` (quat `x y z w`, ganha sobre `euler`), `scale`.
Sem câmara no mundo → auto-orbit lenta na origem.

### Terreno (Fase 1)

Port do `bevy_mesh_terrain` (MIT) corrigido + contratos do plugin terrain do
VibeGame (sampler CPU único, skirts + frontier normals em vez de stitching,
LOD com histerese, pads com falloff, tint por altura/inclinação em vertex
colors — sem WGSL custom).

| Tag | Atributos próprios |
|-----|--------------------|
| `Terrain` | `heightmap` (PNG 8/16-bit; ausente = procedural determinístico via `seed`; `.ahgt` ainda não decodifica → fallback procedural com warning), `world-size` (256), `max-height` (50), `chunk-size` (64), `resolution` (64 — verts/chunk edge), `levels` (3), `lod-distance-ratio` (2.0), `lod-hysteresis` (1.2), `render-distance` (sem default = tudo), `skirt-width` (0.015625), `skirt-depth` (1.0), `height-smoothing` (1 = Catmull-Rom monotone; 0 = bilinear), `collision-resolution` (64; 0 desliga — dados prontos para a Fase 3), `texture`/`texture-url`, `texture-tile-size` (0 = auto), `seed` (0), tint: `base-color`, `color-low`, `color-mid`, `color-high`, `color-rock`, `snow-height`, `slope-threshold`, `slope-softness`, `height-blend-strength` |
| `TerrainPad` | `at` (`"x z"`), `size` (`"w d"`), `falloff` (8), `corner-radius` (4), `height` (ausente = auto: amostra o centro e escreve de volta) |
| `Lake` | `at`, `radius` (6), `depth` (1.5), `water-offset` (0.5), `color` (#2f7a9a), `opacity` (0.78), `ripple` (0.6, reservado). Carve lower-only: contorno orgânico (±28 %, harmónicos sin 2θ/3θ/5θ com fase por posição), rim = mínimo de 32 raios, taça `rim − depth·(1−t²)^1.5` até `radius·1.25`; espelho de água em `rim − water-offset` |
| `River` | `path` (`"x z x z …"`, ≥2 pontos), `width` (6), `depth` (1.5), `water-offset` (0.3), `bank-width` (2), `bank-height` (0.9), `color` (#2a6685), `opacity` (0.85). Chaikin ×2 + estações de 3 m; eixos amostrados pós-pads e suavizados; superfície = prefixo-mínimo descendente (água nunca sobe); carve por estação em 2 passes — banks (raise, teto `MAX_BANK_RAISE`=2) e depois canal+bank cut (lower-only) |
| `Road` | `path` (≥2 pontos), `width` (2), `profile` (artery), `flatten` (true; `false` = trilho decal sem carve), `flatten-falloff` (8), `flatten-window` (56), `flatten-max-grade` (0.22), `flatten-shoulder` (0), `platform-sink` (0.12), `smoothing` (2), `closed` (false), `texture-url`, `texture-scale` (6), `edge-feather` (1.0); aceites sem efeito: `edge-noise`, `end-feather-start/end`, `normal-map-url` |
| `RoadNetwork` | `default-profile` (artery), `default-width` (4), `crossing-flare` (false — alarga ×1.45 perto de ways com grau ≥3), `flatten`, `flatten-falloff`, `flatten-window`, `flatten-max-grade`, `texture-url`, `texture-scale` (9) + filhos `Way id xz [width]` e `Segment a b [via] [width] [profile]` (1 estrada por segmento, width interpolada; `profile="bridge"` salta o carve e desenha deck plano; `bridge-url`/`bridge-lod*`/`bridge-native-span` aceites sem efeito até glTF) |

**Ordem de carve (contrato do VibeGame, `features.rs`):** Pads → Lakes → Rivers →
Roads (arteriais primeiro, **pontes por último**). Estradas saltam núcleos de pads
e zonas de carve de água (mutuamente exclusivas; o guard do road devolve `+inf`
em zona bloqueada). Todo o mutate passa pelo brush engine (`brush.rs`): modos
blend/lower/raise, journal por owner (`pad:0`, `road:3`…) com revert para
re-carve idempotente, guard anti-lip (clamp lower-only ao anel de stencil) e
`min_effective` (larguras < 1.5 texéis são promovidas — senão o carve no-op).

Runtime: `TerrainFeaturesPlugin` (bootstrap one-shot: heightmap → carve
pads→água→estradas → entidades de chunks/água/ribbons) + `TerrainPlugin` (LOD
dinâmico por distância da câmara com histerese, rebuild com budget/frame, cull
por `render-distance`). Queries de gameplay: `TerrainRuntime::sample /
in_water / on_road` (recurso) + `WaterBody::contains / is_near / surface_y_at`
(`avoid-water` / `near-water`) e `RoadPath::is_on_road / distance_to_road`.

**Desvios conhecidos vs VibeGame** (documentados, nenhum afeta o simple-rpg):
estações de road a 1 m (vs 0.35); sem berms/cross-slope; decks de ponte são
ribbons planas (GLB chega com glTF); `.ahgt` não decodifica. Mundo demo:
`worlds/terrain.xml` (`viber analyze worlds/terrain.xml`).

**Regras:**
- Tags case-insensitive; vetores `"x y z"` com broadcast de 1 valor; **2 valores = erro**.
- Bools tolerantes: bare (`<PointLight shadows>`) e `true/1/yes/on` / `false/0/no/off`.
- `<Include src>`: profundidade máx. 8, ciclos fail-fast; caminhos com `/` resolvem
  contra o dir do ficheiro raiz, relativos contra o dir do ficheiro que inclui;
  fragmentos com raiz `<world>`/`<scene>` contribuem os filhos.
- Atributos desconhecidos = **warning** (impresso no `analyze`); tags desconhecidas = **skip no-op** com relatório no `analyze` (`--strict` trata como erro).
- `world`/`scene` aninhados e `<Include>` não-expandido = erro.
- Números não finitos (`NaN`/`inf`) são rejeitados; includes podem sair da árvore
  de pastas (`..`, symlinks) — CLI local, sem sandbox (decisão consciente).

## ROADMAP

- **Fase 0 (✅):** parse/validate, includes, primitivas, luzes, `OrbitCamera`, `run`/`analyze`.
- **Fase 1 (terreno ✅):** heightfield chunks + LOD + pads/água/estradas (`src/terrain/`).
  Falta: glTF (`GltfScene`) + player/movimento; `.ahgt` ainda não decodifica (fallback procedural).
- **Fase 2:** Luau/mlua (hooks `on_add`/`on_update`/`on_remove` + hot-reload).
- **Fase 3:** física avian (`RigidBody`/`Collider` — consumir `collision-resolution`) + simple-rpg atualizado em `Viber/examples/simple-rpg/`.

**Nota:** `script="ficheiro.lua"` já é aceite no XML e registado na IR, mas ainda
**não executa** (chega na Fase 2).
