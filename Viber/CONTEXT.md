# CONTEXT.md — Viber

Contexto de alto nível do projeto (o "quem somos e onde estamos"). Regras de
trabalho e mapa detalhado de ficheiros: [`AGENTS.md`](AGENTS.md).

## O que é

Engine de jogo **nativa** em Rust/Bevy 0.20 (0.20.0-rc.2 via git tag `[patch.crates-io]`)
que corre mundos declarativos XML do AiGameKit — sem browser, sem three.js. É o
equivalente nativo do motor TS anterior (TypeScript/three.js): mesmos mundos, mesmos
contratos de terreno, render wgpu. Nomenclatura segue **Bevy**
(`translation`, `euler`, `half-size`, `base-color`), não Unity/three.js.

## Estado (2026-10)

- **Fases 0–3 ✅** — parse XML → IR → spawn (0); terreno volumétrico
  (voxel/SDF meshado por transvoxel; pads/água/estradas/carve + LOD) (1);
  scripts Luau sandboxed com API `viber.*` / `viber.ui.*` (2); física Rapier
  declarativa (3).
- **Só estático:** o runtime só carrega `<world static="true">` — o
  `world.xml` (+ includes) É a fonte da verdade, com os dados binários em
  `world.data/` (heightmap + pintura, vegetação `.vinst`, `bake.json`).
  Edição por operações (`viber scene`, `viber tool`, `viber terrain`, offline
  ou `--live`) com undo, em paridade total com o ViberEditor. `viber bake` é
  só a conversão única, no sítio, de mundos procedurais antigos. Contrato
  completo no `AGENTS.md` ("Mundos estáticos + edição por agentes").
- O exemplo `examples/simple-rpg` é **jogável de ponta a ponta**: combate,
  27 quests, economia, travel, save/load, mundo vivo, física — portado em 10 loops.
- Scripts Luau com **hot-reload** ao gravar (`src/hot_reload.rs`;
  `VIBER_HOT_RELOAD=0` desliga).
- **Gaps conhecidos (fila aberta):** GPU instancing para vegetação.

## Como correr

```bash
cd Viber && cargo run -- analyze <world.xml>   # valida headless (exit 1 em erro)
cd Viber && cargo run -- run <world.xml>       # janela Bevy
cd Viber && cargo run -- bake <world.xml>      # converte um procedural antigo, no sítio
cd Viber && cargo run -- scene ls              # editar mundos (offline ou --live)
cd Viber && cargo test                          # testes headless
make test-viber                                 # atalho monorepo
```

Sem caminho, `run`/`analyze`/`scene`/`tool` descobrem o mundo por ordem:
`world.xml` → `worlds/*.xml`.

CLI instalado (`./install.sh viber` na raiz do monorepo): `viber create |
analyze | run | bake <world.xml> [--verify] | scene (ls/get/find/set/
insert/rm/mv/dup/apply/undo/redo/catalog/add/drop/files) | terrain (sample/
brush/paint) | tool (list/scatter/settle/canonicalize) | debug … | session …`.
QA headless:
`viber run <mundo> --bridge` + `viber debug screenshot|tree|logs|prof|lua`
(BRP sobre HTTP, porta 15702).

## Mapa do código

| Pasta/módulo | Papel |
|--------------|-------|
| `src/xml/` | parse XML, includes, parsers tolerantes de valores |
| `src/recipes/` | IR de entidades (nomenclatura Bevy) + spawn |
| `src/scene/` | documento de cena + ops com inversas (edição agentes/editor): `doc`, `ops`, `pose`, `live`, `sync`, `pick`, `cli`, `files`, `catalog`, `tools` (geradores), `terrain` (pincéis) |
| `src/bake.rs` | conversão procedural → estático no sítio (mesmos ficheiros + `<stem>.data/`) |
| `src/vinst.rs` | binário `.vinst` de vegetação pré-gerada (zlib; posição/quat/escala/template) |
| `src/app.rs` | `build_app`: lê mundo + `config.yaml`, monta plugins, especializa shaders |
| `src/terrain/` | terreno volumétrico: sampler/carve (`brush`, features), colunas voxel meshadas por transvoxel (`voxel/`), LOD, pintura (`paint`), edições vivas (`delta`) |
| `src/ui/` | UI declarativa (XML + CSS-like + Luau), menus e modais |
| `src/hud/` | widgets bevy_ui vivos: minimapa, compasso, prompt, balão, profiler |
| `src/bridge/` | debug bridge BRP/HTTP + REPL Luau + `viber.scene.*`/`viber.tool.*` (`scene.rs`, `selection.rs`) |
| `src/luau/` | runtime Luau + API `viber.*` (módulos: `host`, `ctx`, `commands`, `runtime`, `api`…; referência: `docs/LUA_API.md`) |
| gameplay soltos | `player.rs`, `camera.rs`, `combat.rs`, `skills.rs`, `quests.rs`, `economy.rs`, `menus.rs`, `save.rs`, `travel.rs`, `vitals.rs`, `feedback.rs` |
| mundo vivo | `worldsys.rs`, `ambient.rs`, `sky.rs`, `postfx.rs`, `animation.rs`, `music.rs`, `particles.rs` |
| física/IA | `physics.rs`, `physics_fx.rs`, `ai.rs`, `spawner.rs` |
| docs/exemplos | `docs/` (LUA_API.md, UI.md, BRIDGE.md), `examples/simple-rpg/` (mundo de referência), `worlds/` (demos) |

## Relação com o monorepo

- Assets GLB do pool partilhado (`examples/shared-assets/public`) vêm
  meshopt/KTX2-comprimidos e a engine **descomprime à leitura**
  (`src/meshopt.rs`) com fallback multi-root (`config.yaml`) — não há
  espelho nem cópias por exemplo (guarda: `tests/asset_pool_dedup.rs`).
- Contratos de terreno portados do plugin de terreno original; desvios
  conhecidos documentados no `AGENTS.md` (raiz).
- O `analyze` imprime o relatório de cobertura de tags — é o roteiro do que
  falta à engine.

## Regras de casa

- **Multi-agente:** verificar mtimes antes de editar (há agentes paralelos a
  trabalhar neste repo); **nunca commitar**; esperar `cargo check` a 0 erros
  antes de verificação visual; engines simultâneas podem esgotar VRAM —
  verificar o log antes de culpar código.
- **Só estático:** editar `world.xml`/`world/**` via `viber scene`/`viber tool`
  (offline/`--live`) ou ViberEditor — nunca hand-edit à espera de
  seating/settle/geração em runtime (não existem; `place`/spawners num
  estático são erro de parse). Depois de pincéis de terreno: `viber tool settle`.
- **XML self-closing:** acrescentar filhos a `<Entity … />` cria um *irmão*
  afixado à origem (= spawn do player); converter para forma aberta — o
  `analyze` **não** apanha isto.
- **Docs em sync:** alterar `src/luau/`/`src/ui/` obriga a atualizar
  `docs/LUA_API.md` / `docs/UI.md` no mesmo cambio.
