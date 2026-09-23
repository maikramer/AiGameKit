# CONTEXT.md — Viber

Contexto de alto nível do projeto (o "quem somos e onde estamos"). Regras de
trabalho e mapa detalhado de ficheiros: [`AGENTS.md`](AGENTS.md).

## O que é

Engine de jogo **nativa** em Rust/Bevy 0.19 que corre mundos declarativos XML
do AiGameKit — sem browser, sem three.js. É o equivalente nativo do VibeGame
(TypeScript/three.js): mesmos mundos, mesmos contratos de terreno, render wgpu.
Nomenclatura segue **Bevy** (`translation`, `euler`, `half-size`,
`base-color`), não Unity/three.js.

## Estado (2026-09)

- **Fases 0–3 ✅** — parse XML → IR → spawn (0); terreno heightfield com
  pads/água/estradas + LOD (1); scripts Luau sandboxed com API `viber.*` /
  `viber.ui.*` (2); física Rapier declarativa (3).
- O exemplo `examples/simple-rpg` é **jogável de ponta a ponta**: combate,
  21 quests, economia, travel, save/load, mundo vivo, física — portado em 10 loops.
- **Gaps conhecidos (fila aberta):** sem hot-reload de scripts; nametags de HUD comentadas
  (`BISECT` em `src/main.rs`); vegetação sem GPU instancing.

## Como correr

```bash
cd Viber && cargo run -- analyze <world.xml>   # valida headless (exit 1 em erro)
cd Viber && cargo run -- run <world.xml>       # janela Bevy
cd Viber && cargo test                          # testes headless
make test-viber                                 # atalho monorepo
```

CLI instalado (`./install.sh viber` na raiz do monorepo): `viber create |
analyze | run | debug …`. QA headless: `viber run world.xml --bridge` +
`viber debug screenshot|tree|logs|prof|lua` (BRP sobre HTTP, porta 15702).

## Mapa do código

| Pasta/módulo | Papel |
|--------------|-------|
| `src/xml/` | parse XML, includes, parsers tolerantes de valores |
| `src/recipes/` | IR de entidades (nomenclatura Bevy) + spawn |
| `src/terrain/` | heightfield: sampler, carve (pads/lagos/rios/estradas), chunks, LOD |
| `src/ui/` | UI declarativa (XML + CSS-like + Luau), menus e modais |
| `src/hud/` | widgets bevy_ui vivos: minimapa, compasso, prompt, balão, profiler |
| `src/bridge/` | debug bridge BRP/HTTP + REPL Luau (`viber debug …`) |
| `src/luau.rs` | runtime Luau + API `viber.*` (referência: `docs/LUA_API.md`) |
| gameplay soltos | `player.rs`, `camera.rs`, `combat.rs`, `skills.rs`, `quests.rs`, `economy.rs`, `menus.rs`, `save.rs`, `travel.rs`, `vitals.rs`, `feedback.rs` |
| mundo vivo | `worldsys.rs`, `ambient.rs`, `sky.rs`, `postfx.rs`, `animation.rs`, `music.rs`, `particles.rs` |
| física/IA | `physics.rs`, `physics_fx.rs`, `ai.rs`, `spawner.rs` |
| docs/exemplos | `docs/` (LUA_API.md, UI.md), `examples/simple-rpg/` (mundo de referência), `worlds/` (demos) |

## Relação com o monorepo

- Assets GLB do pool partilhado (`examples/shared-assets/public`) vêm
  meshopt/KTX2-comprimidos — o espelho `examples/simple-rpg/assets/` guarda
  cópias decomprimidas (regenerar com `scripts/sync_assets.py`; bevy 0.19 não
  lê essas sintaxes; meshopt também decodifica em runtime via `src/meshopt.rs`).
- Contratos de terreno portados de `VibeGame/src/plugins/terrain/`; desvios
  conhecidos documentados no `AGENTS.md` (raiz).
- O `analyze` imprime o relatório de cobertura de tags — é o roteiro do que
  falta à engine.

## Regras de casa

- **Multi-agente:** verificar mtimes antes de editar (há agentes paralelos a
  trabalhar neste repo); **nunca commitar**; esperar `cargo check` a 0 erros
  antes de verificação visual; engines simultâneas podem esgotar VRAM —
  verificar o log antes de culpar código.
- **XML self-closing:** acrescentar filhos a `<Entity … />` cria um *irmão*
  afixado à origem (= spawn do player); converter para forma aberta — o
  `analyze` **não** apanha isto.
- **Docs em sync:** alterar `src/luau.rs`/`src/ui/` obriga a atualizar
  `docs/LUA_API.md` / `docs/UI.md` no mesmo cambio.
