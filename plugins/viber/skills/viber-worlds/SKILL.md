---
name: viber-worlds
description: Usar ao criar ou editar mundos do Viber (engine nativa Bevy do AiGameKit) — world.xml declarativo, config.yaml, tags XML (Terrain, Lake, River, Road, StaticSpawner, Composition, Prototype, UiRoot), atributos universais (script, collider, rigidbody), mundos estáticos com viber bake, edição via viber scene, scripts Luau (API viber.*). Viber world authoring, declarative XML, Luau scripting.
---

# Viber — autorar mundos

O Viber (`Viber/` no monorepo AiGameKit) é a engine nativa Rust/Bevy que corre mundos
declarativos XML. Nomenclatura **Bevy** (`translation`, `euler`, `half-size`,
`base-color`) — nunca Unity/three.js. Fases 0–3 ✅ (XML+spawn, terreno volumétrico,
Luau, física Rapier).

Doc canónica no checkout: `Viber/AGENTS.md` (contrato completo), `Viber/docs/`
(LUA_API.md, UI.md, ASSETS.md, COMPOSITION.md). Este skill é o resumo operacional;
os detalhes de tags/atributos vivem em `references/xml-contract.md` e o mapa da API
Luau em `references/luau-api-map.md`.

## Regra de ouro: o estático manda

- `world.xml` procedural é só **GERADOR**. `viber bake world.xml` produz o mundo
  estático: `world.static.xml` + includes espelhados em `static/**` +
  `bake/terrain.baked.ahgt` + `bake/bake.json`.
- É o **estático** que agentes e editor editam; `viber run`/`scene`/`analyze`
  abrem-no por omissão (auto-descoberta prefere `world.static.xml`).
- `viber bake` **recusa** sobrescrever o estático sem `--force` — e `--force`
  **PERDE as edições** feitas no estático. Regra: editar o estático;
  re-gerar só quando o procedural muda e se aceita perder edições.
- `<world static="true">`: o runtime não assenta nem coloca — as poses do XML são
  finais. `<StaticSpawner>` e `place` num estático são avisos do `analyze`.

## Fluxo de trabalho do agente

```bash
viber create <nome>            # scaffold <nome>/{world.xml,config.yaml}
# editar o XML (contrato em references/xml-contract.md)
viber analyze <world.xml>      # valida headless: assets, conflitos, tags — exit 1 em erro
viber bake <world.xml>         # gera world.static.xml + static/** + bake/
viber scene ls                 # árvore com seletores (abre o estático por omissão)
viber scene set name=well translation="4 0 3"           # edição offline
viber scene --live set name=well translation="10 8 25"  # engine a correr (undo/redo)
```

Edição = operações atómicas (`src/scene/ops.rs`): `set`/`replace_attrs`/`set_text`/
`insert`/`remove`/`move`/`duplicate`, em lotes (`scene apply ops.json`), cada uma com
inversa. Seletores: `world/village.xml#3/0` (ficheiro+path) ou `name=well` (único).
Pose escrita sempre canónica: `translation`/`euler` (graus)/`scale`.

Dentro do checkout do Viber, `viber run` delega a `cargo run` (rebuild da engine);
`analyze` nunca delega. `viber run` corre em **release** por omissão (dev = 8 fps).

## config.yaml é OBRIGATÓRIA

Ao lado do `world.xml`, todo jogo tem de ter `config.yaml` — o contrato de paths
(asset roots extra, bgm/sfx/texturas/scripts/save). O `run`/`analyze` **falham sem
ela**. `viber create` gera uma completa. Schema: `Viber/docs/ASSETS.md`. URLs
`/assets/...` resolvem contra as asset roots: a pasta do jogo SEMPRE 1.ª, as roots
extra do config.yaml a seguir (tipicamente o pool partilhado). Texturas de runtime
do pool são **KTX2/UASTC** — conteúdo novo entra já comprimido.

## Scripts Luau

`script="caminho.lua"` (atributo universal) liga uma entidade a um chunk em
`<dir-do-mundo>/scripts/` (subpastas OK: `enemies/wolf.lua`).

- Hooks: `on_update(dt)` e opcional `on_player_attack(px, pz)` (aggro-chain 15 m).
- O top-level corre **1× por path** (globals partilhados entre entidades com o
  mesmo script — estado por entidade em `viber.state()`).
- **LOD de IA**: além de `activation-radius` (default 45 m) o `on_update` nem corre.
  Controllers sempre ativos: `tag="always-active"`.
- Setters enfileiram comandos aplicados pós-frame; orçamento 500 ms/entrada e
  heap 512 MiB por VM; erros dão warn 1× e a engine segue.
- **Hot-reload** ao gravar o `.lua` (re-corre o top-level; `viber.state()` sobrevive;
  erro de compilação mantém o chunk antigo). `VIBER_HOT_RELOAD=0` desliga.
- API completa: `Viber/docs/LUA_API.md` (~30 grupos: perceção, movimento, IA,
  combate+FX, quests, vault, timers, input, eventos, `viber.game()`, `viber.ui.*`,
  `viber.spawn_prototype`, `viber.own_action/own_system`, terreno vivo `viber.terrain.*`).
  Resumo em `references/luau-api-map.md`.

## UI declarativa

`<UiRoot>` (árvore: `uitext, uibutton, uibar, uilist, uimodal, uirow, uicolumn,
uigrid, uicooldown, uiicon, uicheck, uislider, uiinput…`) + `<UiStyle>` (CSS-like:
paleta Tailwind por nome, `:hover/:active`, `@keyframes`, `vw/vh`, `@media`).
Scripts manuseiam via `viber.ui.*` (eventos, `create/destroy`, `query/rect`,
`set_text`, `set(nome, valor)`). Guia: `Viber/docs/UI.md`; vitrina
`examples/simple-rpg/ui-showcase.xml`.

## Erros comuns

- Vetores `"x y z"` com broadcast de 1 valor; **2 valores = erro**.
- `<Include src>` depth ≤ 8, ciclos fail-fast; `world`/`scene` aninhados = erro.
- Tags desconhecidas = skip no-op com relatório no `analyze` (`--strict` = erro) —
  mundos escritos para o VibeGame parseiam e degradam com garcia.
- Atributos desconhecidos = warning (não conta o `texture=` num `<Entity>` puro).
- O `analyze` audita assets (GLB/texturas/heightmaps/BGM/scripts ausentes, Draco/
  Basis não suportados — meshopt É expandido), quests e conflitos estrada × água/
  cliff. Correr sempre antes de `run`/bake.
- `scene`/`bake`/`tool` exigem binário rebuilt da fonte (`./install.sh viber`) se o
  instalado for anterior a 2026-10-13.

## Referências

- `references/xml-contract.md` — tabela de tags + atributos universais + regras
- `references/luau-api-map.md` — mapa da API `viber.*` por categoria
