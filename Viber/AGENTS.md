# AGENTS.md — Viber

Engine de jogo NATIVA em Rust/Bevy 0.19 que corre mundos declarativos XML do
AiGameKit — sem browser, sem three.js. Estado: **Fases 0–3 ✅** (parse → IR →
spawn → terreno → Luau → física; port do simple-rpg feito em 10 loops).
Nomenclatura segue **Bevy** (`translation`, `euler`, `half-size`, `base-color`),
não Unity/three.js.

## WHERE TO LOOK

| Tarefa | Ficheiro(s) | Notas |
|--------|-------------|-------|
| CLI (`run` / `analyze`) | `src/main.rs` | `analyze` é headless, exit 1 em erro; `run()` monta os plugins por ordem |
| `config.yaml` do jogo (contrato de paths) | `src/config.rs` | `GameConfig` OBRIGATÓRIO ao lado do `world.xml` (asset roots extra, bgm/sfx/texturas/scripts/save) — lido no `run`/`analyze`; schema em [`docs/ASSETS.md`](docs/ASSETS.md); `viber create` gera um completo |
| Auditoria de assets | `src/audit.rs` | corre no `analyze`: GLBs/texturas/heightmaps/BGM/scripts/estilos ausentes, Draco/Basis (não suportados; meshopt é expandido), magia inválida, glTF sem collider; estradas × lagos/rios/cliffs (lâmina/banda, pontes só nas pontas); `--strict` falha com ficheiros ausentes |
| XML: parse, includes, valores | `src/xml/` | `include.rs` (expansão), `values.rs` (parsers tolerantes) |
| IR de entidades + spawn Bevy | `src/recipes/` | `mod.rs` (IR + `KNOWN_TAGS`), `spawn.rs`, `transform.rs` (euler→quat) |
| Interiores (bolsa fora do mapa) | `examples/simple-rpg/world/interiors.xml` (**gerado**), `tools/gen_interiors.py`, `scripts/building-portal.lua` (**gerado**) | Uma `<InteriorScene>` põe 9 salas numa bolsa FORA da pegada do heightmap, onde não há terreno, bioma nem chuva. O XML das salas, o registo porta↔saída do script de portais e o retângulo da bolsa saem todos da MESMA tabela em `tools/gen_interiors.py` (mudar a bolsa = mudar `POCKET` lá). NPCs por papel com fallback para o elenco de rua; `interior-folk.lua`/`interior-keeper.lua` são as FSM de sala |
| Terreno (specs, sampler, mesh, LOD) | `src/terrain/` | `spec.rs` (contrato), `sampler.rs`/`heightmap.rs` (altura), `mesh.rs` (chunks), `plugin.rs` (LOD runtime), `runtime.rs` (bootstrap + carve), `cliffs.rs` (cliffs procedurais + sharpen + CliffMask) |
| Scripts Luau + API `viber.*` | `src/luau.rs` | referência completa em **`docs/LUA_API.md`**; hooks `on_update(dt)`/`on_player_attack`; "LOD de IA" via `ScriptActivation` |
| Hot-reload de scripts | `src/hot_reload.rs` | watcher (`notify`) sobre `<mundo>/scripts/` — recarga ao gravar com re-corrida do top-level; erro de compilação mantém o chunk antigo; `VIBER_HOT_RELOAD=0` desliga |
| UI declarativa (`UiRoot`/`UiStyle`) + `viber.ui.*` | `src/ui/` | `tree.rs` (XML→bevy_ui), `style.rs` (stylesheet), `palette.rs` (cores Tailwind), `anim.rs` (movimento), `widgets.rs` (check/slider/input/tooltip/cursor), `script.rs` (API Luau), `bind.rs` (bindings), `modal.rs` (modais autorais) |
| HUD de jogo | `src/hud/` | widgets que desenham dados do mundo: minimapa, compasso. O profiler e os painéis/menus vivem na UI declarativa (`src/ui/`, `world/profiler.xml`) |
| Player + câmara | `src/player.rs`, `src/camera.rs` | WASD/setas + Shift sprint + Space salto; third-person com drag/scroll |
| Combate | `src/combat.rs`, `src/skills.rs`, `src/feedback.rs`, `src/vitals.rs` | melee [J], alvo [V], skills [C]/[R]/[B]/[L], dano flutuante/i-frames/respawn, HP/XP |
| Colheita (destructibles) | `src/harvest.rs` | minerar/cortar nativos: `destructible="…"` no XML + `<ResourceNode>` no template; [J]/clique perto do prop toca clip `mine`/`chop` com picareta/machado na mão; `fall` = árvore cai e fica toco, `shatter` = pedra despedaça; loot → vault/XP/quests |
| Quests & diálogo | `src/quests.rs` | 21 quests JSON embutidas via `include_str!`, flow [E], QuestTracker |
| Economia & menus | `src/economy.rs`, `src/menus.rs` | vault real + hotbar [1]/[2]; toasts, banner e loading screen. O modal [Q] e a loja passaram para a UI declarativa (`world/menu.xml`); `MenusOpen` é espelhado de `UiModalsOpen` |
| Save/load | `src/save.rs` | JSON em `~/.local/share/viber/<mundo>.save.json` (paths via crate `dirs`); [J]/[L] com um menu aberto, ou os botões Guardar/Carregar do separador Sistema (`viber.ui.action("save"\|"load")`) |
| RNG determinístico | `src/rng.rs` | SplitMix64 ÚNICO da engine ("mesma seed, mesmo mundo") — spawner/terreno/IA/clima; sequência congelada por golden test. `splitmix64` counter-style para o heightmap procedural |
| Travel & wayfinding | `src/travel.rs` | A Nota [F], viagem rápida [G], registry de hostis (alimenta `viber.alive_in_region`) |
| Mundo vivo | `src/worldsys.rs`, `src/ambient.rs`, `src/sky.rs`, `src/postfx.rs`, `src/animation.rs`, `src/music.rs` | DayCycle/Weather/WorldBorder/BiomeRegion como recursos; fog/tint/gestos de NPC/SFX; céu WGSL especializado por mundo; exposure/bloom/SSAO; clips glTF; BGM crossfade |
| Física | `src/physics.rs`, `src/physics_fx.rs` | Rapier (`bevy_rapier3d`): `collider`/`rigidbody` declarativos; knockback cinemático + destrutíveis |
| IA sem script | `src/ai.rs` | wander/chase determinístico p/ criaturas de `DynamicSpawner` sem script + respawn |
| Debug bridge + REPL Luau | `src/bridge/` | `mod.rs` (métodos BRP `viber.*`), `lua.rs` (método `viber.lua` + API `viber.debug.*`), `client.rs` (cliente std-only), `logs.rs` (layer de tracing); QA headless via `viber debug …` |
| Sessão partilhada de QA | `src/session.rs` | lease atómico por mundo (`create_new` + TTL) + `engine.json`; comandos `viber session …`; protocolo obrigatório na secção "Sessão partilhada de QA" |

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
viber create <nome>           # scaffold <nome>/{world.xml,config.yaml} (falha se a pasta existe)
viber analyze [world.xml]     # valida headless; sem caminho procura world.xml / worlds/*.xml
viber run [world.xml]         # janela Bevy em RELEASE; `--debug` e `--no-cargo` disponíveis
viber prune [--debug]         # housekeeping manual do target/ (o mesmo do `run`)
viber --version | help
```

**Housekeeping do `target/` no `viber run`** (`src/prune.rs`): o arranque do
run remove do perfil OPOSTO os **executáveis de build/teste** (cada binário de
teste do Bevy em dev pesa ~2.2 GB de debug symbols) e os **caches
incrementais** — reconstruíveis num relink de segundos, porque o cargo deteta
a falta do output e refaz só o link. Rlibs, `.fingerprint` e outputs de build
scripts FICAM (`cargo test` volta a pronto num relink, não numa
recompilação). Guards: `VIBER_PRUNE=0` desliga; com um cargo/rustc ALHEIO em
curso a limpeza salta-se inteira (o cargo ancestral de `cargo run` é
excluído); binários em execução de dentro do perfil a limpar são poupados.
`viber prune [--debug]` dispara a mesma limpeza sem abrir janela.

**`viber run` corre em RELEASE por omissão.** O motor é Bevy + Rapier: com o
perfil dev o `simple-rpg` media **8 fps**, com release **~90 fps** na mesma
GPU. `--debug` volta ao perfil dev (compila mais depressa, joga muito pior) e
existe para iterar em código, não para jogar. O `Cargo.toml` também passou a
optimizar as dependências no perfil dev (`[profile.dev.package."*"]
opt-level = 3`), pelo que mesmo `--debug` já não é o desastre de antes.

`viber run` dentro de um checkout do Viber delega em `cargo run -- run <mundo>`
(parcidade com o `vibegame run`, que reconstrói a engine) — o binário instalado
corre directo fora do checkout. `analyze` nunca delega (CI-ready, mesmo parser
do binário instalado).

### Texturas: KTX2 obrigatório

Um PNG/WebP é comprimido em disco e **RGBA8 na VRAM**. Todos os assets de
runtime do pool estão em KTX2/UASTC; conteúdo novo deve entrar já assim
(`text3d finish`, ou `scripts/ktx2_compress_pool.py --assets <raiz> --loose`).
Nunca `etc1s`: o Bevy 0.19 não descomprime BasisLZ. Detalhe e números em
[`docs/PERFORMANCE.md`](docs/PERFORMANCE.md). Assets partilhados vivem SÓ no
pool — sem cópias nem symlinks por exemplo ([`docs/ASSETS.md`](docs/ASSETS.md)).

### SSR de reflexões raster (`VIBER_WATER_SSR`, `src/water_ssr.rs`)

Reflexo de cena por raymarch screen-space (Fase B das reflexões). Passe
fullscreen no Core3d (ANTES do TAA — o acumulo
temporal denoiza o reflexo; padrão `fullscreen_material`) que lê
depth+normal prepass: água detectada por
COTA (`TerrainRuntime.water`), raio refletido pela normal (com ondas), 24
passos + bisseção, Fresnel; sem hit mantém o espelho do IBL. **CHÃO MOLHADO:
com `<Weather rain>`, superfícies para cima ganham o mesmo reflexo × chuva ×
0.55 — o look "rua molhada" sem material novo.** **DEFAULT ON** (caminho
raster oficial); `VIBER_WATER_SSR=0` desliga (A/B de QA). WGSL
self-contained em `shaders/water_ssr.wgsl` (escrito no `run`); harness
`tests/water_ssr_shader.rs` valida layout (288/176 B) e proíbe indexação
dinâmica de arrays em uniforms (crash do compilador NV 595.84 — cotas em
campos s0..s7 desenrolados).

### Exposição automática com teto na noite (`NIGHT_LIFT_CAP_EV`, `src/postfx.rs`)

O `AutoExposure` (r2) expõe para a luminância média da cena — mas o céu e a
névoa vivem na **escala da paleta** (o domo é material custom e nunca recebe a
exposição física; `ambient.rs` usa a mesma escala para o fog se fundir com o
horizonte, ver `sky.rs`). De noite o mundo físico fica ~6 stops abaixo do
meio-cinza, o medidor satura no fundo do histograma e abre os +6 EV do MÁXIMO:
a paleta noturna (azul-escuro ~0.04) ×64 = **frame branco** (medido
2026-09-10: `qa-visual` às 23:00 → 235/237/242; o pântano do simple-rpg com
chuva idem). A `AutoExposureCompensationCurve` implementa
`alvo(x) = −x` limitado a **`NIGHT_LIFT_CAP_EV` = 0.5 stops** abaixo de
`NIGHT_LIFT_KNEE_EV` = 3 EV de luminância média, com rampa entre o joelho e o
dobro (o shutter não dá degrau) — ou seja: o dia, o crepúsculo, a sombra funda
e os interiores ficam EXACTAMENTE como eram (o alvo de sempre) e a noite fica
presa no teto. Sem o joelho, o teto escurecia também a alvorada aprovada.
Medido no simples-rpg (pântano, chuva 0.9): frame **127/255** com 2.0,
**83/255** com 0.5 (noite seca) e 90/255 no `qa-raster` (chuva 0.9).
`VIBER_NO_AECURVE=1` devolve a curva plana (A/B do branco da noite).
O fog de distância e o volumétrico NÃO são a fonte do branco (removidos ao vivo,
o frame muda ~1/255) — é a exposição.

### Névoa e visibilidade de noite (`src/ambient.rs`, `src/postfx.rs`)

A névoa é VISIBILIDADE: os multiplicadores de **noite e chuva na densidade do
`DistanceFog` ficaram subtis** (`(1 + 0.08·night)·(1 + 0.12·rain)`; eram +35 %
e +60 %, que no pântano à chuva punham o ~63 % de névoa aos ~90 m — "não se vê
nada"). A base (`FOG_BASE_DENSITY`, ref. 340 m) e o `fog-density` do bioma no
XML continuam a mandar, e o DIA fica intacto. Mais decisivo: o **volume de
god-rays** (`FogVolume`, `src/postfx.rs`) leva `FOG_VOLUME_NIGHT_ATTENUATION`
(escala com o `day`) e fica **desligado à noite** — de noite nenhuma luz o
acende (`light_attenuation = exp(−densidade × raio do AABB × (abs+scat))` ≈ 0,
sol e lanternas incluídas) e ele só encobria a distância; no pântano levava
`FOG_VOLUME_SWAMP_BONUS` extra, ou seja era o pior caso. Medido no `qa-raster`
à chuva: o contraste da cena sobe de sd 3.2 para 4.7 (= a referência com
`VIBER_NO_VOLUMETRICS=1`) e o frame de 89/95/103 para 93/103/117.

### Probes regionais (`VIBER_PROBES`, `src/probes.rs`)

Bounce de bioma nos pads: probes nascem AUTOMATICAMENTE nos `<TerrainPad>`
cujo `<BiomeRegion>` declara `tint` (máx. 8, espaçados ≥40 m; pad sem tint
não gera probe — seria clone do céu mundial). Cubemap pintado em CPU pelo
MESMO painter do IBL (`crate::ibl::sky_cubemap_tinted`, tint no hemisfério de
baixo), repintado por fase do dia; parallax correction por AABB é nativa do
Bevy 0.19. `VIBER_PROBES=0` desliga.

### Debug bridge (`viber run --bridge`)

BRP sobre HTTP (`bevy_remote`) — porta **15702** por omissão; `--bridge PORT`
fixa-a, `--bridge` sem valor escolhe a primeira porta LIVRE a partir de 15702
(duas engines de mundos diferentes nunca disputam a mesma porta) — o equivalente nativo do tooling Chrome DevTools MCP do
VibeGame. Métodos JSON-RPC: `viber.ping` (devolve `pid` + `world` servido); `viber.screenshot` +
`viber.screenshot_status` (request/poll — a captura completa em ~1-3 frames);
`viber.tree` (árvore de entidades: id/nome/pai/transform/componentes);
`viber.logs` (ring-buffer de tracing, 1000 entradas); `viber.input.key/text/
click/move` (input sintético: `KeyboardInput`/`MouseButtonInput`/`CursorMoved`
+ `ButtonInput`); **`viber.lua`** (avalia Luau NA engine — o "evaluate
script" do bridge, secção abaixo). Os métodos BRP builtin (`world.query`,
`world.spawn_entity`, `world.insert_components`, `world.mutate_components`,
…) também ficam expostos — inspecção e mutação live do ECS.

Cliente CLI (`--port` → `--world` → `VIBER_BRIDGE_PORT` → **porta da sessão
viva** → 15702). Desde que `session up` escolha a primeira porta livre, o
cliente descobre-a sozinho pelo `engine.json` da sessão — não é preciso
exportar `VIBER_BRIDGE_PORT` no fluxo normal. A descoberta é rápida (pre-check
TCP de 250 ms por sessão, probe HTTP só na vencedora), imprime `viber: bridge
descoberto em :PORT (sessão: …)` no stderr, prefere a engine cujo mundo está
debaixo do cwd e — como o `viber run --bridge` também registra o seu
`engine.json` — encontra ATÉ engines lançadas fora de `session up`.

**Várias engines vivas do mesmo checkout (agentes com `worlds/qa-*.xml`):** a
escolha implícita é um **ERRO** com a lista `:porta — mundo` — escolher a de
porta mais baixa mandava comandos para a engine de outro agente, em silêncio.
Aponta a TUA engine com `--world` (caminho do XML, nome de ficheiro ou stem —
`viber debug --world qa-pontes lua '…'` resolve a porta pelo `engine.json` da
sessão desse mundo e valida a identidade que a engine reporta no ping; registo
stale → erro, não mutação do mundo errado), ou com `--port`, ou exporta
`VIBER_BRIDGE_PORT`. `viber debug probe` é a exceção: com ambiguidade LISTA as
engines vivas em vez de falhar. O `viber run --bridge` sem valor escolhe a
primeira porta LIVRE a partir de 15702 (a linha de arranque sugere o comando
`--world` certo); `--bridge 15702` fixa a porta:

```bash
viber run worlds/qa-pontes.xml --bridge &   # porta livre automática
viber debug probe                           # bridge vivo? (lista engines se houver várias)
viber debug --world qa-pontes probe         # aponta ESTA engine (stem do mundo)
viber debug --world worlds/qa-pontes.xml screenshot -o shot.png
viber debug screenshot -o shot.png          # captura da janela (1 engine viva = descobre sozinho)
viber debug tree [--json]                   # entidades (como take_snapshot)
viber debug logs [--limit N] [--json]       # console
viber debug prof [--json]                   # snapshot do profiler (fps/frame/
                                           #   entidades/scripts ativos/chunks)
viber debug prof --tab mundo|fisica|audio|extras|tudo   # tabs ricas do painel
viber debug prof --export [ficheiro.json]  # JSON completo para ficheiro
viber debug prof --samples 10              # média/pior/melhor de N amostras — o
                                           #   `fps` de UMA amostra é instantâneo
                                           #   e oscila (12 vs 60 no mesmo mundo)
viber debug click 400 300 [--button right]
viber debug move 400 300
viber debug key w | space | esc | up | ctrl | f3 [--shift]
viber debug text "hello"                   # typing sintético por char
viber debug lua '<código>'                 # avalia Luau NA engine (REPL; [--file
                                           #   f.lua] [--json] [--port] [--world m])
```

### Luau na REPL (`viber debug lua`, método `viber.lua`)

O "evaluate script" do bridge (`src/bridge/lua.rs`): compila o chunk numa env
persistente (globals sobrevivem entre chamadas; `return` devolve o valor) na
VM do `LuaScriptHost`, com o **player como self** — a API `viber.*` dos
scripts de jogo (log, quest_*, toast, teleport_player, …) funciona na REPL.
Resposta: `{ok, result|error, applied, warnings}`. Leituras vêm de um
snapshot do início da chamada; escritas aplicam no MESMO frame (antes dos
sistemas de gameplay). Sem guard de instruções — `while true do end` congela
o frame (igual a um script de página no Chrome; risco aceite).

API de debug `viber.debug.*` (além de toda a `viber.*` dos scripts):

| Leitura (snapshot) | Escrita (mesmo frame) |
|--------------------|------------------------|
| `entities([raio])` → `{id,name?,x,y,z,disabled}` (cap 4096, mais perto 1.º) | `set_pos(id, x, y, z)`, `move_to(id, x, z)` (Y no terreno) — id = bits numérico OU nome (exato→substring) |
| `info(id)` → tudo; `components(id)` → nomes; `transform(id)` → `{x,y,z,pitch,yaw,roll,sx..,gx..}`; `mesh(id)` → `{vertices,indices,has_uvs,uv_count,uv_min/max,topology}`; `material(id)` → `{base_color,metallic,roughness,unlit,base_color_texture,normal_map}` (texturas `[w,h]`); `collider(id)` → `{shape,hx..hz,radius,vertices,shapes}` | |
| `find(nome)` → id, `find_all(nome)` → ids | `teleport(x,y,z)`, `tp(x,z)` (player, Y no terreno), `move_player(dx,dz)` |
| `pos(id)`, `distance(a,b)` → x,y,z / metros | `hide/show/toggle_vis(id)`, `disable/enable(id)` (componente `Disabled`), `despawn(id)` |
| `player()` → `{id,x,y,z,hp,max_hp,xp,xp_next,speed}` | `heal/damage(n)`, `set_hp(n)`, `kill(id)` (HP a zero, sem i-frames), `xp(n)`, `give(item,n)` (vault), `set_speed(n)` |
| `time_scale()`, `fps()`, `prof()` → snapshot do profiler | `set_time_scale(n)` (slow-mo/pausa), `face(x,z)`, `toast(msg)` |
| `stats()` → agregados do mundo (meshes/colliders/luzes/shadows/rigidbodies/emitters/scripts/fps); `physics()` → tempos do último step do Rapier (step/collision/solver/ccd ms) | `colliders([raio])`, `lights([raio])` → dumps (cap 256); `around(raio, limite?)` → resumo compacto de TUDO perto do player (cap 128, mais perto 1.º) |
| `camera()` → `{x,y,z,distance,pitch,yaw,target?}` | `set_camera{distance=?, pitch=?, target=?}` (OrbitCamera — enquadra screenshots) |
| `clock()` → `{minute,dawn,dusk,minutes_per_real_second}` | `set_clock(minute)` (0–1440 — noite p/ screenshots: 1380); `set_window(w, h)` redimensiona a janela p/ QA responsivo (`@media`, `vw/vh`) |
| `ground_state()` → tuning de splat + pele das paredes correntes | **Sol e chão AO VIVO** (sem rebuild): `sun{yaw=?, pitch=?, illuminance=?, shadows=?}` roda a DirectionalLight E o sol do shader do terreno; `ground{moss=?, vale_soft=?, streaks=?, rock_darken=?, tri_slope=?, tri_soft=?, strata_strength=?, patchiness=?, gravel=?, dirt=?, forest=?, shore_width=?}` — paredes ao vivo + re-cozedura dos splats (manchas, cascalho, praia); knobs acumulam entre chamadas |
| `vault()` → `{gold,wood,stone,items{}}` ou nil | `rotate(id, graus)` (yaw, soma), `set_scale(id, s)` (uniforme) |
| `quests()` → `{id: "not_taken\|active\|ready\|done"}` | `spawn_box/spawn_sphere(x,y,z, tamanho [, "#hex"])` — marker `debug:*`; `clear_markers()` remove todos |

Ex.: `viber debug lua 'viber.debug.tp(0, -20) return viber.debug.player()'`;
desativar um inimigo: `viber debug lua "viber.debug.disable('goblin')"`.

Referência completa da API Lua (incl. `viber.debug.*`): **`docs/LUA_API.md`**.

#### Receitas de QA (fluxo típico de agente)

```bash
# 1. Saber onde o herói está (leitura em snapshot; números integrais chegam como inteiros)
viber debug lua 'local p = viber.debug.player() return { p.x, p.y, p.z, p.hp }'

# 2. Levar o herói a um marco (tp senta o Y no terreno) e ver o resultado
viber debug lua 'viber.debug.tp(0, -20) return true'
viber debug screenshot -o depois-do-tp.png

# 3. Congelar um inimigo por nome (sem matar — mantém posição/estado)
viber debug lua 'viber.debug.disable("wolf") return #viber.debug.find_all("wolf")'
viber debug lua 'viber.debug.enable("wolf") return true'

# 4. Debug de economia/vitals: dar itens, curar, XP, slow-mo
viber debug lua "viber.debug.give('potion', 3) viber.debug.heal(25) viber.debug.set_time_scale(0.2) return true"

# 5. Marcar posições no mundo (markers visíveis; nome único debug:sphere:N / debug:box:N)
viber debug lua 'viber.debug.spawn_sphere(12, 3, -8, 0.5, "#ff0044") return true'
viber debug lua 'viber.debug.clear_markers() return true'   # limpa todos

# 6. Screenshot de noite, de cima, com slow-mo e freeze de inimigos
viber debug lua 'viber.debug.set_clock(1380) viber.debug.set_camera{distance = 12, pitch = 40} viber.debug.set_time_scale(0.2) return true'
viber debug screenshot -o cena-noite.png

# 7. REPL com estado entre chamadas + resposta crua
viber debug lua 'marca = {x = 12, z = -8} return true'
viber debug lua --json 'viber.debug.tp(marca.x, marca.z) return viber.debug.time_scale()'
# → {"ok": true, "result": 1.0, "applied": 1, "warnings": []}

# 8. Diagnóstico de performance: agregados + física + o que há à volta
viber debug lua 'local s = viber.debug.stats() local p = viber.debug.physics()
return { fps = s.fps, frame_ms = s.frame_ms_avg, fisica_ms = p.step_ms,
         luzes_com_sombras = s.lights_with_shadows, colliders = s.colliders,
         scripts_ativos = s.scripts_active }'
viber debug lua 'local a = viber.debug.around(30, 128) return #a'   # densidade à volta
viber debug lua 'local l = viber.debug.lights() local n = 0
for _, luz in ipairs(l) do if luz.shadows then n = n + 1 end end return n'  # luzes caras
```

**Limites honestos do profiling:** o Bevy 0.19 não expõe tempos POR sistema
nem POR entidade de render (os executores já não emitem spans tracing). O que
existe de real: `physics()` (contadores do último step do Rapier), `prof()`
(frame ms/fps/scripts/chunks) e os PROXIES de composição em `stats()` — nº de
luzes com sombras, colliders trimesh, emissores, scripts ativos. Para caçar
regressões: comparar `stats()`/`prof()` antes e depois da mudança suspeita.

Notas: leituras (`pos`, `player`, `entities`) refletem o INÍCIO da chamada —
uma escrita e a sua leitura de verificação têm de ser chamadas separadas
(ou ler o mundo via `viber.tree`/screenshot). `disable` insere o componente
`Disabled` do Bevy: a entidade sai de TODAS as queries normais (IA, render,
movimento) mas continua no mundo — `enable` restaura. A env da REPL é única
e partilhada entre chamadas; scripts de jogo têm envs próprias (isolamento
do runtime mantém-se).

### Sessão partilhada de QA (`viber session`, `src/session.rs`)

Uma engine por MUNDO, partilhada por todos os agentes paralelos, com lease
de uso explícito — várias engines simultâneas esgotam a VRAM da GPU
("Quitting the application due to OutOfMemory RenderError" sem panic).
Estado em `<cache>/viber/session-<slug-do-mundo>/`: `lease.json` (criado
com `create_new` — o mutex é o SO) e `engine.json` (pid/porta/mundo/log).

```bash
viber session status                 # SEM SESSÃO | LIBERADO | OCUPADO por X | engine MORTA
viber session list                   # TODAS as sessões: mundo, porta, viva/morta, dono
viber session up [--port N]          # sobe a engine partilhada + espera o bridge (log em ~/.cache/viber/logs/)
                                     #   sem --port procura a 1.ª porta LIVRE a partir de 15702
                                     #   (dois mundos na mesma porta = 2.º bridge morto)
viber session claim --owner <tarefa> # exit 0 = conseguido; exit 3 = OCUPADO (não girar!)
viber session release --owner <tarefa>  # liberta (o --owner tem de bater)
viber session touch --owner <tarefa>    # renova o TTL (default 300 s)
viber session down                   # desce a engine (sem lease ativo)
```

**Protocolo obrigatório para QA ao vivo:**

1. **Nunca lançar `viber run --bridge` directamente** com QA em mente —
   `viber session status` primeiro; se "SEM SESSÃO", `viber session up`.
2. Antes de tocar na engine: `viber session claim --owner <nome-da-tarefa>`.
   Se responder **OCUPADO (exit 3)**: faça OUTRO trabalho e volte mais tarde;
   no máximo uma vez use `claim --wait 120` para esperar pela libertação.
3. Faça os 1–2 testes (`viber debug lua/screenshot/...`) e **`release`
   imediatamente**. QA longa renova com `touch`. O lease expira sozinho
   (TTL 300 s) se o agente morrer — quem vem depois rouba-o sem cerimónia.
4. **Nunca matar processos de engine** — a única via legitima é
   `viber session down` (e exige sessão livre).
5. Engine "MORTA" no status → `viber session down && viber session up`.

Fluxo típico:

```bash
viber session up
viber session claim --owner qa-sky || return    # exit 3 → fazer outro trabalho
VIBER_BRIDGE_PORT=15702 viber debug lua 'viber.debug.tp(0, -20) return true'
VIBER_BRIDGE_PORT=15702 viber debug screenshot -o qa.png
viber session release --owner qa-sky
```


### Profiler (P) — painel declarativo

**P** abre o painel do profiler — agora na stack declarativa (`src/ui`), com
as mesmas 5 abas do `?profiler=1` do VibeGame: **Sistemas** (timings por
sistema + grupos + scripts Luau por ficheiro), **Mundo** (player/câmara/
entidades próximas com tags), **Física** (Rapier: corpos/colisores/sono/step),
**Áudio** (buses/layers/sinks) e **Extras** (toggles: wireframe de colisores,
relva, pausar física). Cada aba tem **COPIAR** (JSON completo → clipboard) e
**EXPORTAR** (ficheiro em `$TMPDIR/viber-profiles/`).

Anatomia:
* Engine: `src/profiler/` (`mod.rs` plugin+contadores+overlay F3, `timed.rs`
  wrapper `timed(Group, system)` que mede sistemas — o Bevy 0.19 não expõe
  tempos por sistema — mais âncoras do `PhysicsSet::StepSimulation`,
  `world_tab.rs`/`physics_tab.rs`/`audio_tab.rs` snapshots,
  `script.rs` = `viber.profiler()`/`viber.profiler_cmd()` para Luau);
* UI: `examples/simple-rpg/world/profiler.xml` + `ui/profiler.css` +
  `scripts/ui/profiler.lua` (modal `key="p"`, abas `tab-group`, listas
  `<UiList bind="prof-*">` alimentadas pelo driver);
* Bridge: `viber.profiler` (leve/compat), `viber.profiler.tab
  {"tab":"systems|world|physics|audio|extras|all"}` e
  `viber.profiler.export`/`extra_toggle`. O JSON de `"all"` é O MESMO do
  botão COPIAR e do ficheiro — um só construtor (`full_snapshot`).

Teclas: **P** (modal), abas por **clique** ou teclado nativo do modal
(**`]`/`.`** próxima, **`[`/`,`** anterior, **1–5** saltam), **F12/Pause**
(congelar), **`** (exportar), **PgUp/PgDn** (raio das próximas). A fonte da
verdade das abas é o widget nativo; o driver espelha para a engine. Overlay mínimo **F3**
mantém-se. `VIBER_PROF_LOG=1` liga o `LogDiagnosticsPlugin`.

QA típico: `viber debug prof --tab física`, `viber debug prof --tab tudo
--json`, `viber debug lua 'return viber.profiler().state'`. (O P do profiler
é toggle do modal; o P que aprende talento só existe com o menu [Q] aberto na
tab Talentos.)

### Teclas (jogo)

| Tecla | Ação | Código |
|-------|------|--------|
| WASD/setas, Shift, Space | mover (rel. à câmara), sprint ×1.5, salto (buffer + coyote) | `player.rs` |
| rato drag / scroll | câmara yaw/pitch / zoom (clamp 2–80 m) | `camera.rs` |
| J ou clique esq. | ataque melee (cone + alcance, XP por kill); perto de colhível (`destructible`) = minerar/cortar com a ferramenta na mão; confirma em menus; **grava o save** na tab Sistema | `combat.rs`, `harvest.rs`, `save.rs` |
| E | interação (diálogo/colheita/loja); fora de alcance de NPC = skill cura | `player.rs`, `skills.rs` |
| V | ciclar alvo | `combat.rs` |
| C / R / B / L | dash / golpe radial / bomba / guard (segurar) | `skills.rs` |
| Q | menu de jogo (UiModal declarativo em `world/menu.xml`) — tabs Missões/Mochila/Talentos/Loja/Controlos/Sistema; P aprende talento na tab Talentos; ↑↓ navegam, ←→ volumes | `ui/modal.rs`, `menus.rs`, `save.rs` |
| K | loja (tab Loja do menu) perto do mercador; fora dele é debug +10 XP | `menus.rs`, `vitals.rs` |
| F / G | assinar marco da Nota / viagem rápida em fogueira | `travel.rs` |
| 1 / 2 | hotbar: poção / antídoto | `economy.rs` |
| H / N | debug: −10 HP / cura total | `vitals.rs` |
| F9 / F10 / F11 | debug: harvest de quest / give de economia / teleport ao próximo marco | `quests.rs`, `economy.rs`, `travel.rs` |
| Esc | fecha modal (o tecla do modal autoral é configurável: `key="…"` aceita q/e/i/m/k/j/l/b/c/tab/esc/f1–f3) | `menus.rs`, `ui/modal.rs` |

Detalhes: handlers correm como sistemas exclusivos em `RemoteLast` (depois de
`Last`); screenshots são request+poll porque a captura precisa de frames de
render (bloquear o handler congelaria a engine); o cliente HTTP é std-only
(`src/bridge/client.rs`, retry no connect pois o bind é assíncrono). Código:
`src/bridge/` (`mod.rs` server, `client.rs` cliente, `logs.rs` layer de
tracing). Testes headless em `src/bridge/tests.rs` (App mínima + bridge real
em loopback).

## CONTRATO XML (Fase 0)

Raiz: `<world>` (ou `<scene>`), attr `clear-color` (`#rgb`/`#rrggbb`/`0x…`/nome).
Ao lado do `world.xml`, TODO jogo tem de ter um **`config.yaml`** — o contrato
de paths com a engine (asset roots extra, dirs de áudio/texturas/scripts,
save), lido no arranque do `run`/`analyze` (falham sem ele): schema e regras
em **[`docs/ASSETS.md`](docs/ASSETS.md)**; `viber create` gera um completo.

| Tag | Atributos próprios |
|-----|--------------------|
| `Entity` / `Group` | contentor transform-only (hierarquia via filhos) |
| `Composition` | **objeto inteiro em primitivas** — 1 corpo (default `fixed`; `none`/`dynamic`/`kinematic`) + colisor EXATO por primitiva (default `collider="auto"`; `none` desliga), luzes/partículas como filhos; attrs próprios `place` (`"at: x z; align-to-terrain: …; base-y-offset: …"` — colocação explícita, reescreve XZ e amostra o solo) e `seat="none"` (flutuante, sem seating); assenta como BLOCO (nunca divide as partes). Partes: `Box` (alias de `Cuboid`)/`Sphere`/`Cylinder`/`Plane`/`Capsule` + sub-pivots `Group`; material por parte com `opacity` (0=invisível, <1=Blend) e `emissive`; `overlap-max` (VibeGame) aceite e ignorado. **Guia completo: [`docs/COMPOSITION.md`](docs/COMPOSITION.md)** |
| `Prototype` / `Use` | definir UMA vez (`<Prototype id="…">` — qualquer árvore como raiz; recolhido de todo o mundo expandido, ordem não importa; não spawna) e instanciar N vezes (`<Use prototype="…" name/script/collider/body/pos/euler/scale/…>` — deep-clone com overrides no root; aninhado com corte de ciclo; `--strict` falha com id desconhecido). `analyze` reporta definições/instâncias |
| `Cuboid` | `half-size` (vec3) |
| `Sphere` | `radius` |
| `Cylinder` | `radius`, `half-height` |
| `Plane` | `half-size` (vec2, plano XZ) |
| `Capsule` | `radius`, `half-height` |
| `PointLight` | `color`, `intensity` (default 1200 lm), `radius`, `shadows` |
| `DirectionalLight` | `color`, `illuminance` (lux, default bevy 10 000), `direction` ("x y z", para onde a luz viaja; −Z da entidade alinha à direção), `shadows` |
| `AmbientLight` | `color`, `brightness` — aplicado como recurso `GlobalAmbientLight`, não entidade |
| `OrbitCamera` | `target` (nome de entidade), `distance`, `height`, `pitch` (graus; quando presente sobrepõe `height` via `height = distance·tan(pitch)`) |
| `GltfScene` | `url` (obrigatório; `/assets/...` resolve contra as asset roots por ordem — a pasta do jogo SEMPRE 1.ª, as roots extra do `config.yaml` a seguir (tipicamente o pool partilhado); guarda anti-regressão `tests/asset_pool_dedup.rs`, ver **[`docs/ASSETS.md`](docs/ASSETS.md)**) + attrs universais; cena default do GLB spawna como filhos da entidade (transform aplica); load assíncrono, falha = warn + nó vazio. GLBs do pipeline vêm meshopt-comprimidos — o `MeshoptAssetReader` (`src/meshopt.rs`) descomprime EXT_meshopt à leitura; NÃO há espelho nem cópias por exemplo |
| `StaticSpawner` | `count`, `seed`, `region-min`/`region-max` ("x y z"), `density-per-km2`+`max-instances` (modo de contagem alternativo: `count = densidade × área km²` da região XZ, teto `max-instances` — 0 = sem teto; `count` explícito ganha), `cluster-count`/`cluster-radius`, `footprint-radius`+`avoid-overlaps` (0 = AUTO: meia-largura XZ do AABB do GLB, fallback 0.8 m), `max-slope-deg`, `max-slope-attempts` (32 — tentativas POR instância; esgotadas, a instância é omitida), `avoid-water`, `in-water` (à lâmina de água), `near-water`+`near-water-radius`, `avoid-road` (default ON), `avoid-cliff` (default ON — nada nasce em parede de cliff nem a `cliff-margin` dela; `"0"` restaura), `cliff-margin` (2 m — folga além da vergem da máscara de cliffs, distância REAL medida por `CliffMask::is_cliff_within`), `activation-radius` (LOD de IA dos scripts), `align-to-terrain`, `base-y-offset` (soma ao Y pós-assentamento), `scale-min`/`max`+`scale-axis-min`/`max`, `random-yaw`, `max-distance`; template = primeiro glTF (`GLTFLoader`/`GltfScene`) na subárvore filho. Colocação determinística (SplitMix64 por seed). Espelha `src/spawner.rs` (função pura `compute_placements`) — ver **Spawner: colocação** abaixo |
| `ParticleSystem` | `preset` (fire, smoke, fireflies, ground-dust, sparkle, leaves, snow, sand-dust, magic, core; desconhecido = core) + `transform="pos: x y z"` (component-string) + overrides em `particle-emitter="preset: …; emission-rate: …; start-life-min/max: …; start-speed-min/max: …; start-size-min/max: …; start-color: #hex; looping: …; world-space: …"`. Emissor CPU billboard (`src/particles.rs`): mesh de capacidade FIXA por emissor (quads degenerados nos slots livres — realocar por frame tripava use-after-free no slab allocator), vertex color com fade por vida, material unlit Add (fogo/magic/sparkle/fireflies) ou Blend (resto) |
| `PlayerGLTF` | `model-url` (obrigatório), `name` (default `player`), `pos` (alias de `translation` — tags verbatim mantêm `pos`), `speed`. Componente `Player` + WASD/setas relativo à câmara (Shift = sprint ×1.5, Space = salto com buffer/coyote), assenta no terreno via `TerrainRuntime::sample` todos os frames (`src/player.rs`) |
| `ThirdPersonCamera` | alias interactivo do `OrbitCamera` com defaults `target="player"`, `distance` 4, `height` 1.6; attrs `mouse-sensitivity`, `follow-lag`, `turn-lag`, `min-terrain-distance`, `fov`. Drag (qualquer botão) = yaw/pitch, scroll = zoom (clamp 2–80 m), anti-clip com o terreno; câmaras extra são rebaixadas a Group com warning |
| `DialogueNPC` | `dialogue-id` (obrigatório; ausente = skip), `marker-height` (2.5 — marcador flutuante), `portrait-url`/`voice-sfx` aceites sem efeito. Componente `DialogueNpc` + marcador esférico dourado emissivo; [E] a <3.5 m abre o **diálogo real** (linhas intro/progress/complete das quests JSON, `src/quests.rs`) com balão no HUD |
| `AudioMixer` | `master`/`music`/`sfx` (buses, default 1) — recurso `AudioMixerSettings` (`src/music.rs`); o driver de música multiplica o bus music |
| `MusicLayer` | `layer` (obrigatório; ex. explore/battle/boss/dungeon/mountain/village), `sound` (nome `bgm-x`; URL por convenção `{bgm_dir}/x.ogg`, dir do config.yaml), `base-volume` (0.2). Spawna `AudioPlayer` em LOOP a volume 0; `music_driver` crossfada a layer da zona do player (port do `bgmZone`: dungeon caixa 770-950/205-355 → mountain wedge z≤-240 → vila r<55 → explore) com fade 0.6/s. **Combate**: `CombatMusicState` (`src/music.rs`, escrito pelo aggro da FSM em `src/ai.rs`) promove a layer para `battle` (ou `boss` se o nome da criatura contiver "boss") durante 8 s após o último evento de aggro |
| `ResourceChip` | `resource` (obrigatório), `icon`, `target-entity` — chip de HUD com valor VIVO do vault (`gold/wood/stone`, `src/economy.rs`), empilhado no canto superior esquerdo; `icon`/`target-entity` aceites sem efeito |
| HUD (HudScreenLayer, HealthBar, XpBar, BossBar, TargetBar, Minimap, Compass, InteractionPrompt, DialogueBalloon, TabbedModal, QuestTracker, WaypointArrow) | IR genérica `HudElement` (tag + attrs crus); UI real em `src/hud/` via bevy_ui: barras HP/XP reais (sync de vitals), TargetBar com o alvo do combate, Minimap 140×140 com `range` + borda, Compass, prompt de interação ([E] a <3.5 m ou via `viber.set_interaction`), balão de diálogo, menu de jogo [Q] (UiModal declarativo, `world/menu.xml`) e profiler em janela (F3/P). QuestTracker renderiza o tracker de quests (`src/quests.rs`), WaypointArrow a seta do waypoint (`src/travel.rs`). A tag `BossBar` é aceite mas **sem UI própria** (removida no cleanup) |
| `DynamicSpawner` | mesmos attrs de colocação do `StaticSpawner` (densidade, footprint auto, occupancy e tentativas incluídas); template = `Creature`→glTF. Criaturas sem `script` ficam com IA da engine (`src/ai.rs`: wander/chase determinístico + respawn); com `script` o comportamento é Luau (+ `activation-radius` para o LOD de IA). Ficam sempre verticais — `align-to-terrain` é ignorado (perfil `creature` do VibeGame) |
| `SpawnExclusion` | `at` ("x z"), `radius` — círculo global; registado no **registo de ocupação partilhado** e respeitado por TODOS os spawners (mesmo com `avoid-overlaps="0"`) |

**Spawner: colocação (port do `spawner` do VibeGame).** Todos os grupos de
spawn partilham um **registo de ocupação XZ** (`SpawnOccupancy`, buckets por
célula de 4 m): cada `<SpawnExclusion>` e cada instância colocada registra um
disco; um candidato cujo disco encoste um disco registado — com **folga de
0.6 m** (`SPAWN_CLEARANCE`, props nunca nascem "colados") — é rejeitado. O
teste contra o que já está registado (exclusões, outros grupos) corre SEMPRE;
`avoid-overlaps` liga apenas o registo dos discos do PRÓPRIO grupo (tapetes
densos continuam a compactar entre si). O teste usa `footprint × scale-max`
(conservador); o registo usa a escala real. As colocações de TODOS os grupos
são computadas no MESMO frame (quando o último template resolve), pela ordem
do XML — a ocupação partilhada torna a ordem relevante, e o gate único mantém
a promessa "mesma seed, mesmo mundo" (um template preso em Loading > 60 s é
desistido com warn). Por instância: `max-slope-attempts` tentativas; cada
candidato rejeitado queima uma. Altura = **superfície desenhada** (`TerrainRuntime::sample_mesh_surface`: o
zero do SDF, que é o que o transvoxel renderiza).
Com `align-to-terrain`, a rotação inclina o modelo para a normal amostrada
por **matriz 3×3 ponderada** (`BrushGrid::sample_normal_matrix`, centro 6×,
sondas a 1 texel — estabiliza o tilt e lê o declive real de ravinas) com
inclinação linear de 5°→60° **clampada** (`terrain_align_rotation`): troncos
seguem o declive mas nunca deitam em falésias; o yaw aplica-se sobre o eixo
do tronco antes do tilt. Criaturas de `<DynamicSpawner>` ficam sempre
verticais (movem-se depois do spawn). `profile`/`variation`/`ground-align`/
`pick-strategy` continuam aceites sem efeito.

**Cliffs e anel da pegada — spawn conservador.** Com `avoid-cliff` (default
ON em spawners, `<Vegetation>` e pedras de margem), nada nasce EM parede de
cliff nem a `cliff-margin` (2 m) dela: a closure do spawner amostra a
**máscara regional** (`CliffMask::is_cliff_within`, a mesma que pinta o
triplanar e trava a relva) com folga = margem + footprint × escala máx —
"a 2 m do cliff" conta a partir do sítio onde o prop vai caber. `in-water` é
a exceção habitual. Além disso, todo o candidato que passa os gates do
centro é re-testado num **anel de 8 pontos** com raio `max(footprint ×
escala, 1.5 m)`, lendo a MESMA matriz 3×3 ponderada que alimenta o tilt do
modelo: declive acima do limite, água (com `avoid-water`) ou cliff em
QUALQUER ponto do anel rejeitam o candidato, e um **spread de altura**
centro↔anel > 2 m (`SPAWN_LEDGE_DROP`) rejeita degraus/terraços que cruzam a
pegada com declive médio baixo. Cada rejeição conta o motivo em
`PlacementStats` e `instantiate_spawn_groups` escreve UMA linha por grupo no
log (`spawn '<url>': N placed / M attempts (rejected … — cliff W, water A…)`)
— visível no `viber debug logs`, ideal para afinar margens e regiões.

**Água e estrada — exclusão com exceção.** `avoid-water="1"` (árvores,
pedras, props secos) rejeita o carve de lagos/rios; `avoid-road` é **default
ON** em todos os spawners e `<Vegetation>` (árvores/pedras/erva nunca nascem
no leito das estradas — `avoid-road="0"` devolve o comportamento antigo).
A exceção é `in-water="1"`: só nasce EM água e assenta À **lâmina de água**
(`TerrainRuntime::water_surface_at`), não no fundo escavado — vitórias-régias
e plantas aquáticas flutuam na superfície, ficam verticais e ignoram o
declive do leito (a normal do fundo rejeitava/inclinava mal em lagoas
fundas). `near-water="1"` é o anel de margem em seco (juncos, pedras de
margem); não combinar com `in-water`. `avoid-water` ganha se ambos estiverem
autrados. Zonas de exclusão autorais: `<SpawnExclusion at="x z" radius="n">`
— sempre honrada por TODOS os spawners, mesmo com `avoid-overlaps="0"`.
| `destructible` (attr universal) | component-string de colheita nativa (`src/harvest.rs`): `popup-text`, `popup-color` (#hex), `preset` (burst de break), `burst-count`, `hits` (3), `hit-preset` (sparks/rockshards→sparks, woodchips→leaves), `hit-burst-count`, `shake-on-hit`, `crack-on-hit`+`crack-style` (voronoi/vertical → darken ×0.85 por golpe), `break-style` (`burst` \| `fall` \| `shatter`), `cut-height` (aceite; os GLBs de árvore já vêm pré-divididos em meshes `Stump`+`Top`), `range` (3.5). Num template de spawner aplica-se a CADA instância; filho `<ResourceNode kind="wood\|stone" yield="N"/>` do template define o loot. [J]/clique perto do prop: o herói equipa picareta (mine) ou machado (fall), toca o clip `mine`/`chop`, impacto a 35 % do clip, faíscas+wobble+darken por golpe; no último: `fall` tomba o `Top` longe do player com pivô no corte e fica o toco com collider, `shatter` lança 9 pedaços balísticos que pousam e desvanecem. Loot → vault (`ResourceNode`) + 30 XP + quests de recolha (lêem o vault) + popup flutuante + SFX `chop/mine-hit/break` (`assets/audio/sfx/combat/*.ogg`) |
| `Vegetation` | `meshes` (lista separada por espaços), `density-per-km2`, `seed`, `region-*`, `scale-*`, `max-slope-deg`, `avoid-water`, `avoid-road` (default ON — `avoid-road="0"` deixa a erva entrar nas fitas), `avoid-cliff`/`cliff-margin` (idem spawners, default ON/2 m), `max-distance`, `cluster-*`; count = densidade × área km² com cap `max-instances` (default 800/tag — o original GPU-instancia ~100k; instancing é follow-up). `smart`/`wind`/`flower-*`/`plant-*` aceites sem efeito |
| `Sky` | domo de céu procedural WGSL (`src/sky.rs`: sol, nuvens FBM, lua, estrelas, aurora, meteoros); os attrs crus são injectados como consts no shader **escrito em disco** (`<asset_root>/shaders/sky.wgsl`) a cada `run` — especialização por mundo (o Bevy 0.19 não re-uploads uniforms de material custom). `model` (`analytic`, default — o gradiente estilizado — ou `nishita`, o scattering físico portado do `bevy_atmosphere`) escolhe o modelo; `VIBER_SKY_MODEL` sobrepõe-no sem editar o XML. O modelo é resolvido UMA vez no `run` (`SkyConfig::with_env_override`) e viaja como resource `SkyModelState` para o IBL e os probes regionais — é o mesmo valor no shader e no ambiente, e o arranque loga `sky: modelo …` (o gate fica visível no `viber debug logs`) |
| `DayCycle` | relógio dia/noite que conduz ambiente + sol: `minute-of-day`, `minutes-per-real-second`, `dawn-minute`, `dusk-minute`, `ambient-day-intensity`, `ambient-night-intensity`, `drive-ambient`, `max-sun-elevation`, `sun-azimuth-base` (`src/worldsys.rs`) |
| `Weather` | `wind` (vec2, consts de shader no boot), `wind-strength`, `clouds`, `rain` (0..1 contínuo — intensidade; conduz emissor de chuva ancorado ao player com partículas esticadas `size_y`, loop SFX `ambient/rain_loop.ogg` com volume ∝ intensidade, fog/exposure no `AtmosphereState`), `cycle` (bool — liga o scheduler determinístico: a cada 240 s roda um alvo de chuva com SplitMix64(seed ⊕ índice·golden), transição lerp 10 s; sem `cycle` a chuva é estática como autorada) (`src/worldsys.rs` + `src/ambient.rs`: `RainEmitter`, `rain_emitter_driver`) |
| `BiomeRegion` | polígono de bioma: `id`, `polygon` (`"[x,z;x,z;…]"`), `display-name` (nome de exposição no HUD via bind `zone.name`; ausente = tabela de fallback da engine), `fog-density`, `tint`, `pp-exposure`, `pp-bloom-strength` — fog/tint/postfx seguem a região do player (`src/ambient.rs` + `src/postfx.rs`) |
| `WorldBorder` | clamp da posição do player: `radius` (3800), `warn-seconds` (5), `margin` (80) |
| `NavMesh`, `SpawnGate`, `ProjectileTemplate`, `AdaptiveQuality`, `PostFxDebugToggle` | aceites → recurso `EngineConfigData` (tag + attrs crus, `src/worldsys.rs`) — **data-only**, nenhum consumidor runtime ainda |
| `UiStyle` | stylesheet CSS-like da UI declarativa: texto do elemento ou ficheiro via `src="ui/hud.css"`, relativo à pasta do mundo (`src/ui/style.rs`) — guia: [`docs/UI.md`](docs/UI.md) |
| `UiRoot` | árvore UI declarativa inteira (`src/ui/tree.rs`): elementos `uitext, uibutton, uibar, uilist, uimodal, uirow, uicolumn, uigrid (cols="repeat(4, 1fr)"), uicooldown, uiicon, uispacer, uicheck (toggle), uislider (min/max/step), uiinput (campo de texto)` com attrs `id, class, style, text, value, key, tab, tab-group, scroll, hidden, disabled, escape-closes, bind, anim ("spin/pulse/bob/shake"), tooltip`; folha de estilo com paleta Tailwind por nome (`rose-400/80`), `box-shadow`, grid, decoração de texto, `pointer-events`/`cursor`, estados `:hover/:active/:disabled` e RESPONSIVO: unidades `vw/vh/vmin/vmax` + blocos `@media (min-width: …)/(portrait)/(max-aspect: …)` com re-estilo em resize — guia completo: [`docs/UI.md`](docs/UI.md); `<UiList>` + `<UiTemplate>`/`<UiEmpty>` repetem uma fonte de dados com `{campo}` substituído (fontes da engine OU `viber.ui.list()` de script, que passa a possuir a fonte); scripts manuseiam-na via `viber.ui.*` (`docs/LUA_API.md`); vitrina de tudo em `examples/simple-rpg/ui-showcase.xml` |

Primitivas aceitam material: `base-color`, `metallic`, `roughness`,
`opacity` (0=invisível, <1=`AlphaMode::Blend`), `emissive`/`emissive-color`,
`texture`/`texture-url`, `texture-tile-size` (mipmaps + anisotropia via
`src/textures.rs`) e **`normal-map`/`normal-map-url`** (mapa de normais do pool,
linear — sem ele uma parede texturada fica lisa: o albedo dá o padrão e a luz
continua a bater numa face plana; é o que faz os interiores lerem como
construídos).
Atributos universais: `name`, `tag`, `script` (Luau — ver **Scripts Luau**),
`translation`, `euler` (graus XYZ), `rotation` (quat `x y z w`, ganha sobre
`euler`; com **3 valores é euler em RADIANOS** — compat VibeGame),
`scale`, `collider` (`none`/`auto`/box/sphere/cylinder/capsule/mesh/precompute —
`auto` numa PRIMITIVA dá colisor exato derivado da forma) e
`rigidbody`/`body` (kinematic/dynamic/…) — física declarativa via
`src/physics.rs`. Primitivas idênticas partilham mesh/material (dedup por
bits no spawn).
Sem câmara no mundo → auto-orbit lenta na origem.

### Terreno (100% volumétrico)

O terreno inteiro sai do **campo voxel** (`VoxelField`, SDF `p.y − altura ⊕
mods`) por transvoxel (marching cubes com células de transição — costura de
LOD sem saias), em COLUNAS com ladder de LOD (célula 1→2→4 m,
histerese, budget de caixas/frame, cull por `render-distance`). O heightmap
(PNG/.ahgt/procedural) sobrevive como INPUT — termo-base do SDF e alvo do
carve de pads/lagos/rios/estradas pelo brush engine; não há mesh nem collider
heightfield. O visual do chão tem dois caminhos: o tint LEGADO por
altura/inclinação em vertex colors (sem WGSL) ou — quando o mundo declara
`layers` — o BLEND de 13 texturas de solo do pool por splat map (`splat.rs` +
`layer_material.rs` + `shaders/terrain_chunk.wgsl`), que substitui o tint e
pinta areia nas margens e o LEITO DE SEIXO (`pebbles`) nos fundos de
lagos/rios.

| Tag | Atributos próprios |
|-----|--------------------|
| `Terrain` | `heightmap` (PNG 8/16-bit ou `.ahgt` — decodificado em `heightmap.rs::from_ahgt`; ausente = procedural determinístico via `seed`), `world-size` (256), `max-height` (50), `chunk-size` (64), `resolution` (64 — a célula do LOD0 voxel é
`chunk-size/resolution` = 1 m; o ladder duplica por nível), `levels` (3),
`lod-distance-ratio` (2.0), `lod-hysteresis` (1.2), `render-distance` (sem
default = budget de 2048 colunas), `height-smoothing` (1 = Catmull-Rom
monotone; 0 = bilinear — suaviza a GRID de input), `collision-resolution`
(64; interruptor — **0 desliga os colliders**; já não define geometria), `cliff-angle` (50° — gatilho da
CliffMask/splat; 90 desliga), `cliff-min-area` (120 m²), `cliff-min-drop` (4 m), `cliff-min-extent` (8 m) — filtro REGIONAL: um componente de declive só é cliff se passar os três (mata declives espúrios), `cliff-streaks` (0.5 — escorrimentos verticais na pele da parede), `cliff-moss` (0.35 — musgo procedural nos ombros/ledges), `sharpen` (false), `sharpen-angle` (35°), `sharpen-seed` (0 = deriva de `seed`), `texture`/`texture-url`, `texture-tile-size` (0 = auto), `seed` (0), tint (caminho LEGADO): `base-color`, `color-low`, `color-mid`, `color-high`, `color-rock`, `snow-height`, `slope-threshold`, `slope-softness`, `height-blend-strength`; blend de camadas: `layers` (lista de ≤13 aliases do pool — `grass vale_grass dirt dirt_trail forest_floor gravel mountain_stone sand desert_sand snow_peak swamp_mud dirt_road pebbles` — ou caminhos de textura; o slot do leito carrega `pebbles` mesmo que o mundo não o liste), `shore-width` (5 m — faixa de areia fora da linha de água) — **PRODUÇÃO**: 8 layers por chunk (material bindless — o crash NV foi resolvido), ligado por omissão; escape hatch para o tint legado: `VIBER_CHUNK_LAYERS=0`; pele das paredes (estratos/streaks/musgo) configurável ao vivo via `viber.debug.ground{...}` |
| — | **Material de chunk (r7 — 8 layers, bindless)**: o bootstrap gera UM material por chunk (`generate_chunk_splats` em `splat.rs`): as 8 texturas do pool com MAIOR peso agregado no chunk + DOIS planos splat RGBA8 32² próprios (plano 0 = slots 0–3, plano 1 = slots 4–7; pesos renormalizados a somar 1 EM CONJUNTO; chunks de montanha carregam snow/stone, de pântano mud — áreas diferentes têm blends diferentes). `rock` (paredes), leito (seixo) e a AREIA DA MARGEM são FORÇADOS na eleição top-8 — sem o force da areia a praia quebrava em costuras retas nos chunks que a perdiam da paleta. Material próprio (`layer_material.rs`, NÃO ExtendedMaterial) `#[bindless]` com 8 layers + 2 splats (pares de bindings 1–20; tabela de índices `range(0..21)`; params em storage array na binding 10: tiles/tints/flats/roughs + origem/tamanho + layer de rocha para paredes triplanares) + day/night tint; shader `shaders/terrain_chunk.wgsl` (template embutido reescrito no `run`). ⚠ **porquê bindless**: com bevy 0.19.1 + wgpu 29.0.4 + NV 595.84, QUALQUER material custom NÃO-bindless com `#[texture]` morre com SIGSEGV dentro de `libnvidia-gpucomp` ao criar o pipeline layout — teste-guarda `test_chunk_material_stays_bindless`; o `StandardMaterial` e materiais só-`#[storage]` (céu) funcionam; isolado por bissect de mundos M0–M23 (2026-09-04). Falha de textura reponta o slot para a layer dominante (leito → gravel quando o chunk o carrega) |
| `TerrainPad` | `at` (`"x z"`), `size` (`"w d"`), `falloff` (8), `corner-radius` (4), `height` (ausente = auto: amostra o centro e escreve de volta) |
| `InteriorScene` | `at` (`"x z"`, CENTRO), `size` (`"w d"`) — declara um retângulo como **bolsa de interior**, FORA da pegada do heightmap (`|x|` ou `|z|` > `world_size/2`). Dentro dela a `WorldBorder` não trava, não há bioma (névoa/tinta/exposição neutras) e NÃO chove (o emissor de chuva segue o player e seguiria para dentro da sala). A distância (>2 km) desliga o resto por si — render, cull, IA, spawners — e **fora do campo o terreno não gera colunas nem colliders**. A zona de música `dungeon` deixou de ter caixa copiada à mão: segue esta declaração. Um só por mundo; ver `src/worldsys.rs::InteriorSceneConfig` e `examples/simple-rpg/world/interiors.xml` |
| `Lake` | `at`, `radius` (6), `depth` (1.5), `water-offset` (0.5), `color` (#2f7a9a), `opacity` (0.62 — lido pelo shader como escala de extinção da coluna), `ripple` (0.6 — amplitude das ondas, especializado como `CFG_WAVE_AMP` no `water.wgsl`; o maior dos lagos do mundo vence), `bank` (`soft` \| `beach` \| `cliff` \| `terraced` \| `gorge` \| `overhang` — `gorge`/`overhang` são VOXEL: anel de parede sólida na linha de água (`overhang` soca a base sob a lâmina; o carve preserva o banco natural), os restantes esculpem a rampa no heightfield), `rocks` (false — pedras de margem automáticas), `rocks-density` (0.12/m de linha de água), `rocks-scale-max` (1.4), filhos `<Island at="x z" radius height/>` (repetível — domo RAISE na bacia com praia; o espelho faz fade sobre ela). Carve: contorno orgânico com PERSONALIDADE por lago (`LakeShape` — alongamento dirigido `stretch·cos(2(θ−axis))` + harmónicos k=1,3,5,7 com amplitude e fase sorteadas por hash da posição; uns lagos saem quase redondos, outros ovais com baías e lóbulos, ±45 % no pico = `CONTOUR_PEAK` 1.45), rim = mínimo de 64 raios, taça `rim − depth·(1−t²)^1.5` até `radius·1.25`; espelho de água em `rim − water-offset` e termina EXATAMENTE na linha de água da taça |
| `River` | `path` (`"x z x z …"`, ≥2 pontos), `width` (6), `depth` (1.5), `water-offset` (0.3), `bank-width` (2), `bank-height` (0.9), `color` (#2a6685), `opacity` (0.72), `bank`/`rocks`/`rocks-density`/`rocks-scale-max` (idem `<Lake>`; `gorge` = paredes verticais sólidas dos dois lados, `overhang` = socava), `pool-spacing` (0 — poços ×1.6/rápidos ×0.4 com largura ±20 %; a superfície fica lisa, o LEITO ondula e a profundidade lê-se no shader), `cascades` (true — queda >1.2 m entre estações vira cascata: face de água vertical no mesh, caldeirão ×1.6 a jusante, névoa `mist` na base), `waterfalls` (true — CACHOEIRAS automáticas: queda acumulada ≥ `waterfall-min-drop` (3 m) é o tier acima da cascata — cortina contínua do lip à base alargada ×1.4, caldeirão à escala da queda, névoa escalada, spray no lip, espuma no caldeirão, loop de áudio posicional `water_waterfall.ogg` com raio/ganho ∝ queda), `waterfall-min-drop` (3 — limiar do tier, clamp ≥ CASCADE_DROP), `waterfall-notch` (true — no cruzamento com um `<Cliff>`, fenda de spill no brow: cápsula subtractiva com largura ∝ canal; a parede fica sólida e a água despenca POR CIMA), `spring` (false — nascente na estação 0: ferradura de rocha voxel com a boca a jusante, poozinho fundo, névoa). **Rio × cliff = cachoeira automática**: o pre-pass de specs cruza os paths 2D (`river_cliff_crossings`), o carve segura a superfície a montante da crista e garante a queda a jusante (height do cliff ou 3 m), a deteção pós-bands anota a face brow→toe (`CascadeInfo.wall`); o audit reporta cada cruzamento como ℹ. Confluência: estações dentro do contorno de um lago sobem à cota do espelho. Chaikin ×2 + estações de 3 m; superfície = prefixo-mínimo descendente (água nunca sobe); a ribbon acaba na linha de água real e meia-largura varia por estação (pools) |

**Água viva (automática, sem attrs):** espuma ambiente — emissores `foam`
contínuos ao longo da linha de água de cada corpo (um a cada 6 m, cap 40);
áudio ambiente — loops `water_lake.ogg`/`water_flow.ogg` com volume por
distância à linha de água (fade 0–26 m, bus sfx, `src/ambient.rs`). As
pedras de margem (`rocks="1"`) entram no pipeline de spawner como candidatos
fixos determinísticos (seed = posição do corpo; `src/terrain/shore_rocks.rs`)
— herdam occupancy partilhada, LOD ladder, colliders e `avoid-road`.
| `Cliff` | `path` (`"x z x z …"` — linha de creste; a face pende do lado da queda), `width` (6, percurso horizontal da face), `height` (ausente = **auto**: a diferença natural entre creste e pé — a parede adapta-se ao lugar), `angle` (com `height` autoral deriva `width = height/tan(angle)`), `profile` (`vertical` \| `concave` \| `convex` \| `columnar` \| `terraced` \| `overhang` \| `arch`; columnar = colunas basálticas com cortes retos, fendas e brow dentado — a referência wargame; overhang = saliência que corta para trás por baixo da verga, abrigo com rocha por cima; arch = parede vertical com UM vão em arco furado no meio da banda — cápsula subtractiva na estação média, só abre se a queda local ≥ 3 m), `side` (`auto`/`left`/`right`), `noise` (0.15, ondulação da borda como fração de `width`), `gullies` (0 = off; 0.15–0.4 — ravinas de erosão one-sided na face, bútresses ficam na linha nominal), `notches` (0 = off; 0.1–0.3 — colos no creste como fração da queda LOCAL), `talus` (false — cone de detritos no pé, carve RAISE-only com `talus-angle` 36°; `run ≈ 0.55·queda/tan(ângulo)`, enterra até 35% da queda; o bitset `talus` entra na camada pública da máscara — splat pinta gravel, relva/spawners evitam), `seed` (0). **Sólido 3D no campo voxel — já NÃO é um carve** (`src/terrain/voxel/cliff.rs`). `vertical` é mesmo aprumado, `concave` tem UNDERCUT real (rocha por cima da cabeça), `convex` faz a sobrancelha exceder o próprio pé, `columnar` põe o offset de coluna em geometria. Colisão pelo trimesh da coluna (ver **Colisão de terreno** abaixo). Como a parede saiu da grid, um survey de estrada já não a lê — mantenha os cliffs afastados das artérias (o simple-rpg usa ~30 m). Banda one-side: o lado de cima NUNCA é tocado. Pele da parede (shader por chunk): estratos cromáticos (tint quente↔frio por banda + banco duro 1-em-4), meteorização vertical e AO de contacto lidos do WALL SPACE da máscara (canal R das vertex colors, 0=brow→1=pé), escorrimentos e musgo por noise — doseados por `cliff-streaks`/`cliff-moss`. Journal `cliff:i` (parede) + `cliff:i` (talus) |
| `Cave` | `path` (`"x z x z …"` — eixo do túnel em XZ), `radius` (3 — **um valor ou um perfil**: `radius="2.5 5 3"` estreita, abre numa galeria e volta a estreitar, interpolado por COMPRIMENTO DE ARCO), `depth` (8 — profundidade do CENTRO do tubo abaixo da superfície), `open-ends` (true — a profundidade decresce a zero nas duas pontas, portanto o túnel rompe a encosta e a gruta tem bocas; a false fica selada), `mouth-flare` (1 — multiplicador do raio nas bocas), `mouth-fraction` (0.18 — fração do comprimento em que cada boca sobe à superfície). Filhos repetíveis: `<Chamber at="x z" radius height [depth]>` (sala — elipsóide subtractivo, `at` obrigatório) e `<Shaft at="x z" radius [depth]>` (chaminé vertical até à luz do dia). **NÃO é um carve** — nada é escrito no heightfield. É um encadeado de cápsulas / cones subtractivos no campo voxel (`src/terrain/voxel/cave.rs`), a primeira feature que põe ROCHA POR CIMA da cabeça do jogador. Construída DEPOIS do carve, portanto um túnel sob uma estrada segue o leito da estrada como construído. `depth <` o maior raio avisa (o tubo rompe ao longo de todo o comprimento em vez de ter tecto). Viber-only: o VibeGame salta a tag com aviso |
| `Arch` | `at` (`"x z"`) **ou** `path` (`"x z x z …"`) — exatamente um dos dois; `width`/`span` (vão livre; com `path` deriva do passo menos as pernas), `height` (6 — altura livre do vão na coroa), `thickness` (2.5 — espessura das pernas), `depth` (4 — fundura do bloco ao longo do eixo), `yaw` (0 — ignorado com `path`, manda a tangente), `spans` (1 — N aberturas ao longo do `path`: um viaduto), `profile` (`portal` \| `natural`). Portal de rocha autónomo: sólido union no campo voxel (`src/terrain/voxel/arch.rs`) com vão em arco (caixa + coroa em cápsula); `natural` troca o bloco por uma banda de cones a curvar de pé a pé, grossa no chão e fina no fecho. Com `path` cada perna assenta no chão do SEU pé (a base vai ao mais baixo dos dois), que é o que faz um arco numa encosta ler como natural em vez de flutuar. A coluna no centro do vão tem DOIS spans sólidos — `column()` responde 2 e `viber.ground_below` põe o andador no chão, não na fita. Spawner/relva rejeitam a fita (`has_thin_roof`: laje suspensa < 4 m de espessura). Viber-only: o VibeGame salta a tag com aviso |
| `Bridge` | `path` (`"x z x z …"`, obrigatório — eixo do tabuleiro), `width` (6), `rise` (2 — camber acima da corda entre as duas margens), `thickness` (1.2 — espessura da laje; **< 2 m avisa**, uma laje mais fina que duas células do LOD0 desaparece nos níveis grosseiros), `style` (`stone` \| `natural`), `spans` (auto — número de arcos, só `stone`), `pier-width` (2.5), `parapet` (0.9 — 0 desliga), `clearance` (2 — folga exigida sob o tabuleiro; **reportada, nunca imposta**, e medida como o MAIOR vão que a travessia oferece, porque toda a ponte está enterrada nos encontros). Travessia volumétrica (`src/terrain/voxel/bridge.rs`): o tabuleiro é uma cadeia de caixas union no campo voxel, portanto o trimesh da coluna traz o collider e **o herói anda por cima**. `stone` acrescenta pilares assentes no chão, o intradorso do arco e o tímpano até ao tabuleiro; `natural` é uma cadeia de cones sem pilares nem guardas, grossa nas margens e fina no fecho. **Tudo aditivo, por regra**: nenhuma forma de ponte pode escavar o terreno que atravessa — encher-e-furar parecia mais simples e comia as paredes de um desfiladeiro em V. Um arco só abre onde há vão a abrir (o troço onde o tabuleiro está mesmo livre do chão), portanto uma ponte mais longa que a garganta não põe arcadas dentro da margem. **Não confundir com `<Road profile="bridge">`**: essa desenha um ribbon plano em `deck_y` e não tem collider nenhum. **Para travessias curtas de ESTRADA, prefere uma `<Composition>` de primitivas** (tabuleiro + rampas + parapeitos, `seat="none"` para fixar a cota, `texture-tile-size` igual ao da estrada): o sólido voxel fino degrada nos LODs grosseiros ("meia ponte") e as cabeças em degrau contra os pads de aproche criam quedas — caso real: a ponte norte do simple-rpg (ver `world/bridges/ponte-norte.xml`). O `<Bridge>` voxel fica para vãos de rocha/traçados orgânicos sem estrada em cima. Viber-only |
| `RockFeatures` | `region` (`"minX minZ maxX maxZ"`, obrigatório), `seed` (0), `arches`/`caves`/`bridges` (0 — quantos semear de cada), `min-slope` (22) / `max-slope` (72) em graus, `min-drop` (5 — relevo mínimo na vizinhança do sítio), `spacing` (40 — passo da rede de candidatos E distância mínima entre features semeadas), `clear-of-roads` (10), `min-rise` (2.5 — gate de pé de montanha: o chão tem de SUBIR ≥ isto na direção do túnel). Semeia arcos/grutas/pontes numa região e **resolve em specs `<Arch>`/`<Cave>`/`<Bridge>` normais** no bootstrap (`src/terrain/voxel/scatter.rs`) — nada a jusante vê uma feature nova. Determinista por construção (rede jitterada com `hash01`, sem RNG): mesma seed + mesmo terreno ⇒ mesmas rochas (DENTRO de uma versão da engine — gates novos mudam colocações). **Guards ao SÍTIO e ao PATH inteiro** (todos os tipos): água (`is_near` com folga do raio), corredor de estradas, `CliffMask` (paredes), pads (`<TerrainPad>`) e discos das features JÁ no mundo — autorais (caves/arcos/pontes escritos à mão) e as semeadas por campos irmãos; as rejeições contam-se por motivo e saem UMA linha por campo no log de arranque (`viber debug logs`). As **caves** são o caso apertado (subtraem!): só nascem no pé de encostas (`min-rise`) e o path reamostrado a 4 m nunca pode mergulhar abaixo da boca −1 m nem subir menos que `max(min-rise, depth×0.5)` — as duas bocas abrem em subida, nunca uma vala em plano. Pontes dispensam a banda de declive (o sítio é o fundo do vão) mas os abutments têm de estar secos e o VÃO não pode cruzar parede/pad/feature (água e estrada pode — é para isso que existe). `analyze` conta o campo, não as features: o heightfield ainda não existe no parse. Viber-only |
| `Road` | `path` (≥2 pontos), `width` (2), `profile` (artery), `flatten` (true; `false` = trilho decal sem carve), `flatten-falloff` (8), `flatten-window` (56), `flatten-max-grade` (0.22), `flatten-shoulder` (0), `platform-sink` (0.12), `smoothing` (2), `closed` (false), `texture-url`, `texture-scale` (6), `edge-feather` (1.0); aceites sem efeito: `edge-noise`, `end-feather-start/end`, `normal-map-url` |
| `GroundDecal` | `at` (`"x z"`), `radius` **ou** `size` (`"w d"`, extensão total) ou `half-size`, `feather` (2.5 — banda de alpha para fora da borda, em metros), `noise` (0.1 — ondulação da borda como fração do raio), `seed` (ausente = derivado da posição), `texture`/`texture-url`, `texture-scale` (9, metros por repetição — UV **world-space**, igual às ribbons), `base-color`, `roughness`, `lift` (0.04). Mancha de chão drapejada: anéis concêntricos amostrados no heightfield (não um quad plano), borda ondulada por harmónicos periódicos e alpha 1→0 em smootherstep. **Puramente visual** — nunca toca no heightfield. Substitui os `<Plane>` decal (quadrados duros a `y` fixo que cortam/flutuam no terreno) |
| `RoadNetwork` | `default-profile` (artery), `default-width` (4), `crossing-flare` (false — alarga ×1.45 perto de ways com grau ≥3), `flatten`, `flatten-falloff`, `flatten-window`, `flatten-max-grade`, `texture-url`, `texture-scale` (9) + filhos `Way id xz [width]` e `Segment a b [via] [width] [profile]` (1 estrada por segmento, width interpolada; `profile="bridge"` salta o carve e desenha deck plano; `bridge-url`/`bridge-lod*`/`bridge-native-span` aceites sem efeito até glTF) |

**Ordem de carve (contrato do VibeGame, `features.rs`):** Pads → Lakes → Rivers →
Roads (arteriais primeiro, **pontes por último**) → decals (visuais, leem o
heightfield final) → discos de junção. Cliffs NÃO carvam — são sólidos no
campo voxel (`voxel/cliff.rs`), construídos depois do carve. Um disco de junção coberto por um
`GroundDecal` é **suprimido**: empilhar os dois punha duas camadas de cobble
com alpha a poucos centímetros uma da outra sobre os mesmos UVs world-space —
os feathers somavam-se em costuras e o par brigava no depth. Estradas saltam núcleos de pads
e zonas de carve de água (mutuamente exclusivas; o guard do road devolve `+inf`
em zona bloqueada). Todo o mutate passa pelo brush engine (`brush.rs`): modos
blend/lower/raise, journal por owner (`pad:0`, `road:3`…) com revert para
re-carve idempotente e `min_effective` (larguras < 1.5 texéis são promovidas —
senão o carve no-op). Nota: o `BrushGrid` de produção é deliberadamente
**unguarded** (o clamp lower-only ao anel de stencil fabricava falésias);
a implementação com guard (`HeightSampler::apply_pads`) existe só para testes.

Runtime: `TerrainFeaturesPlugin` (bootstrap one-shot: heightmap → carve
pads→água→estradas → mods voxel → COLUNAS voxel dentro do raio de render →
água/ribbons) + `TerrainPlugin` (ladder de LOD por coluna: select com
histerese, construção staged sob budget de CAIXAS/frame, swap atómico, cull
por `render-distance`, respawn). Queries de gameplay: `TerrainRuntime::sample
/ sample_mesh_surface / in_water / on_road` (recurso — as duas primeiras
devolvem o TOPO do SDF, que é a superfície desenhada) +
`WaterBody::contains / is_near / surface_y_at` (`avoid-water` /
`near-water`) e `RoadPath::is_on_road / distance_to_road`.

**Colisão de terreno — uma só superfície (Fase 2/3 do plano voxel):** o
collider É o mesh: UM trimesh por coluna (`physics.rs::ColumnColliderBake`),
os MESMOS triângulos que o transvoxel desenha, assado no spawn da coluna e no
swap atómico do LOD (mesh e collider trocam no mesmo frame), com
`FIX_INTERNAL_EDGES` (pseudo-normais — sem ghost collision nas arestas
internas). Vive na entidade da coluna. Banda de colisão
`min(chunk_size × 3, lod_distance)` com PISO de LOD 0 dentro dela (âncora:
herói, fallback câmara) — o chão tocável é sempre a geometria fina e nunca
troca de LOD sob o herói. `stream_voxel_colliders` mantém add/repair/remove e
publica `TerrainCollisionStatus` ("há chão carregado?"): o player só usa o
chão analítico (`surface_below`) quando não há collider carregado; com chão
carregado, o collider é a autoridade. **Fora do campo não há terreno (e a
engine tem de o dizer):** as amostras do `BrushGrid`/`VoxelField` saturam na
orla, portanto um XZ além de `±world_size/2` devolvia a cota da BORDA como se
fosse chão — era por aqui que um teleporte para fora do mundo acabava com o CCT
despenetrado ~4 s depois. `TerrainRuntime::in_field(x, z)` é a pergunta que
faltava, guardada em todos os sítios que usam chão analítico
(`player.rs` no resgate do vazio e na rede de segurança, `worldsys.rs::seat_at`,
`camera.rs` no anti-clip), e a janela de colunas desejadas **intersecta** a
grelha em vez de saturar nela: fora do campo nada é construído. É esta regra que
sustenta a `<InteriorScene>`. **Regra dura:** gameplay com Y conhecido usa
`surface_below` (sob um overhang o topo do mundo é TETO);
`grid.sample` fora de `src/terrain/` é proibido (`collision-resolution` = só
interruptor, `0` desliga). Teste: `tests/terrain_collision.rs`.

**Água animada** (`water_material.rs` + `shaders/water.wgsl`): lakes/rivers
usam `ExtendedMaterial<StandardMaterial, WaterExtension>` — o fragment
acrescenta ondas dirigidas pelo `wind`/`wind-strength` do `<Weather>`
(normais animadas), fresnel (transparente de cima, espelho rasante + tint de
céu), e glint de sol/lua (segue o relógio do `<DayCycle>`, igual à luz
real). Zero uniforms de material — consts especializadas por mundo no
`shaders/water.wgsl` (escrito no `run`, ao lado do céu) + `Globals` para o
tempo, mesma arquitetura do `src/sky.rs`. Cor/alpha do corpo nas VERTEX
COLORS (fade de margem incluído); `NotShadowCaster` obrigatório.

**Desvios conhecidos vs VibeGame** (documentados, nenhum afeta o simple-rpg):
estações de road a 1 m (vs 0.35); sem berms/cross-slope; decks de ponte são
ribbons planas (GLB chega com glTF). Mundo demo:
`worlds/terrain.xml` (`viber analyze worlds/terrain.xml`).

**Regras:**
- Tags case-insensitive; vetores `"x y z"` com broadcast de 1 valor; **2 valores = erro**.
- Bools tolerantes: bare (`<PointLight shadows>`) e `true/1/yes/on` / `false/0/no/off`.
- `<Include src>`: profundidade máx. 8, ciclos fail-fast; caminhos com `/` resolvem
  contra o dir do ficheiro raiz, relativos contra o dir do ficheiro que inclui;
  fragmentos com raiz `<world>`/`<scene>` contribuem os filhos.
- Atributos desconhecidos = **warning** (impresso no `analyze`); tags desconhecidas = **skip no-op** com relatório no `analyze` (`--strict` trata como erro).
- **Auditoria de assets no `analyze`** (`src/audit.rs`, headless — lê só cabeçalhos): recolhe TODAS as refs a ficheiros do XML (GLB de scenes/player/spawner templates/vegetation/colliders `mesh-url`, texturas de primitivas/terreno/estradas/decals, heightmaps, BGM por convenção `assets/audio/bgm/<layer>.ogg`, scripts Luau, estilos UiStyle) e verifica: ficheiro ausente (✗ — erro em `--strict`), GLB sem magic "glTF" / com Draco ou Basis (⚠ — a engine não lê; meshopt É expandido pelo asset reader), textura com extensão fora de png/jpg/webp/ktx2/hdr/tga ou magia PNG/JPEG inválida (⚠), áudio fora de .ogg (⚠) e modelos glTF sem collider próprio nem herdado do ancestral (ℹ — passam através; o herói fica fora: character controller próprio). Caminhos resolvem como no runtime: url `/assets/…` contra a asset root; scripts contra `<mundo>/scripts`. Nota: `texture=` num `<Entity>` puro (sem primitiva) é atributo ignorado pelo parser — o audit não o conta.
- **Auditoria de conflitos de features no `analyze`** (`src/audit.rs`): o traçado de cada `<Road>`/`<RoadNetwork>` (segmentos expandidos, translações XZ acumuladas) é testado contra lagos, rios e cliffs SEMPRE contando a **meia-largura do ribbon** (a borda da estrada conta — a linha central sozinha deixava passar trilhos com a borda na água; caso real: o trilho NE da Lagoa Grande do simple-rpg): entrar no contorno orgânico REAL de um lago (`LakeShape::contour`, ±45%) ou na lâmina de um rio (`half + meia-largura`) avisa (⚠) com o ponto do pior cruzamento no rótulo. Atravessar a banda de um cliff (`width/2 + estrada/2 + 1 m`, ⚠) idem. Pontes (`profile="bridge"`) são ⚠ SEMPRE que cruzam água: o deck é ribbon plana **sem collider** no terreno voxel — travessia quebrada para o herói; a travessia real é uma `<Bridge>` volumétrica (Viber-only) fora da rede. Mesmo com pontas secas avisam (apontam o cruzamento); avisam mais forte se as PONTAS ficarem dentro do worst-case do contorno (`radius × CONTOUR_PEAK`) ou dentro da banca de margem do rio (poços abrem ×1.2 com `pool-spacing`). O carve-guard já evita ESCAVAÇÃO em zona de água — o que o audit apanha é o RIBBON submerso/rasgado (caso real: ponte da Lagoa Grande do simple-rpg).
- `world`/`scene` aninhados e `<Include>` não-expandido = erro.
- Números não finitos (`NaN`/`inf`) são rejeitados; includes podem sair da árvore
  de pastas (`..`, symlinks) — CLI local, sem sandbox (decisão consciente).

### Som (SFX + BGM)

- **Registry único** (`src/ambient.rs`): `SfxClip` enum + `SFX_CLIPS_ALL` — 36
  clips `.ogg` com paths `assets/audio/sfx/…`; `SfxHandles` pré-carrega todos
  no `PostStartup`. Os clips base (`hit/whoosh/harvest/ui`) são Text2Sound
  (os stubs `.wav` sintéticos foram removidos). `Hit/Whoosh/EnemyHurt/Footstep
  /FootstepWater/Coin` levam jitter de pitch ±8 % (anti-repetição).
- **Backend kira** (`bevy_kira_audio` 0.26, 2026-09-07): buses tipados
  `MusicBus`/`SfxBus` (`src/music.rs`) alimentados pelo `AudioMixerSettings`
  via `mixer_sync` — sliders/volumes do save respondem AO VIVO (o modelo
  antigo multiplicava o bus no momento do spawn). Crossfade `fade_step`
  linear preservado; conversão `linear_to_db` na fronteira. BGM, loops de
  água/chuva e cachoeiras partem de `AudioLoopPending` → `audio_loop_starter`.
- **Gatilhos nativos**: melee/slam/bomba (swing/hit/enemy_hurt/enemy_death em
  `kill_creature`), dano/morte/parry-guarda do herói (`feedback.rs` →
  hurt/game_over/shield_block), cura/dash/bomba (`skills.rs`), level-up
  (`vitals.rs`), quest accept/complete (`quests.rs`), save/load (`save.rs`),
  loot de vault (`economy.rs`, clip `Loot` = chest_open), salto e passos
  one-shot (`player.rs`), viagem rápida (`travel.rs`), aggro da FSM com voz
  por nome — wolf/slime/boss (`ai.rs`) e nas transições dos scripts.
- **BGM de combate**: `CombatMusicState` (`src/music.rs`) — aggro acende
  `battle` (ou `boss`) durante `COMBAT_MUSIC_HOLD` = 8 s; o `music_driver`
  dá-lhe prioridade sobre a zona.
- **`viber.sound(nome)`** (Luau): registry completo em `luau::SFX_NAME_REGISTRY`
  — ver `docs/LUA_API.md`. Um clip novo no enum SEM linha no registry falha o
  teste `test_lua_registry_covers_all_clips`.
- **Assets**: pool canónica `examples/shared-assets/public/assets/audio/` com
  manifests `manifests/audio-sfx-*.yaml` (Text2Sound/Stable Audio 3; regenerar
  com `regen_audio.py --only <id>`). A engine serve o pool diretamente por
  fallback de roots (`docs/ASSETS.md`) — os clips de áudio nunca são copiados
  para o exemplo (o antigo `sync_assets.py` foi removido; o guarda
  `tests/asset_pool_dedup.rs` parte a build de quem o ressuscitar). Loops de
  água (`water_lake/water_flow`) também são carregados por path fixo
  (`setup_water_ambience`).
- **`analyze` audita o áudio da engine**: os paths acima são verificados nas
  asset roots (`audit::engine_audio_files` + `audit::resolve_asset`) — clip
  ausente = issue (erro em `--strict`).

### Scripts Luau (Fase 2)

`script="caminho.lua"` (atributo universal) liga uma entidade a um chunk em
`<dir-do-mundo>/scripts/` (`src/luau.rs`; caminhos aceitam subpastas —
`enemies/wolf.lua`). Hooks definidos pelo script: `on_update(dt)` e
`on_player_attack(px, pz)` (opcional — aggro-chain: aliados scriptados a
≤15 m do alvo atingido recebem a posição do atacante). A API `viber.*`
(perceção, movimento com snap no terreno, IA wander/chase, combate, quests,
vault, interação, `viber.gesture`/`viber.sound`/`viber.player_hp` para NPC) +
`viber.ui.*` está documentada em **`docs/LUA_API.md`**.

Pontos-chave: o top-level do chunk corre **1× por path** (globals partilhados
entre entidades com o mesmo script — estado por entidade em `viber.state()`);
os setters enfileiram comandos aplicados pós-frame; erros dão warn 1× e a
engine segue; "LOD de IA" — além do raio de ativação (`activation-radius` no
spawner, default 45 m) o `on_update` nem corre. **Hot-reload** (`src/hot_reload.rs`,
`VIBER_HOT_RELOAD=0` desliga): gravar um `.lua` recompila o chunk e re-corre o
top-level nas entidades ativas (globals resetam; `viber.state()` sobrevive);
erro de compilação mantém o chunk antigo. Sem hooks `on_add`/`on_remove` (o
ciclo de vida é top-level na ativação + `on_update`).

## ROADMAP

- **Fase 0 (✅):** parse/validate, includes, primitivas, luzes, `OrbitCamera`, `run`/`analyze`.
- **Fase 1 (terreno ✅):** heightfield chunks + LOD + pads/água/estradas
  (`src/terrain/`); desde 2026-09-06 o terreno é **100% volumétrico**
  (colunas voxel + transvoxel, heightfield só como input/dado).
  `.ahgt` decodifica (header JSON + grid u16 deflate); sem ficheiro → procedural.
- **Fase 2 (Luau ✅):** runtime mlua sandboxed + API `viber.*`/`viber.ui.*`
  (`docs/LUA_API.md`), profiler (F3/P) + bridge BRP; port do simple-rpg em
  10 loops (combate/feedback, quests 21 JSON, economia/vault, UI & menus,
  travel/A Nota, save/load, skills, mundo vivo, física-fx) com os 38 scripts
  de `examples/simple-rpg/scripts/`.
- **Fase 3 (física ✅):** Rapier via `bevy_rapier3d` — colliders/rigidbodies
  declarativos (`collider`/`rigidbody`), character controller cinemático,
  knockback + destrutíveis (`src/physics_fx.rs`); consome `collision-resolution`
  do terreno.

**Fila aberta (conhecida):** tags `EngineConfig` data-only sem consumidor
(`NavMesh`, `SpawnGate`, `ProjectileTemplate`, `AdaptiveQuality`,
`PostFxDebugToggle`); instancing GPU para vegetação. Hot-reload de scripts
saiu da fila (2026-09-07, `src/hot_reload.rs`). Adotações de crates e
watch-list do ecossistema (navmesh rerecast/vleue, replicon, hanabi, …):
**`docs/CRATES.md`**.
(Nametags de HUD — pílulas flutuantes nome+distância sobre NPCs — foram
**removidas** a pedido do autor em 2026-09-06; não recriar sem decisão.)
- **Fotorealismo:** estudo completo + roadmap priorizado em `docs/VISUAL_ROADMAP.md` (2026-09-08)
