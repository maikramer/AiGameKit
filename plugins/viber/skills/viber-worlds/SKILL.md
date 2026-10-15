---
name: viber-worlds
description: Usar ao criar ou editar mundos do Viber (engine nativa Bevy do AiGameKit) — world.xml declarativo, config.yaml, tags XML (Terrain, Lake, River, Road, StaticSpawner, Composition, Prototype, UiRoot), atributos universais (script, collider, rigidbody), mundos estáticos (viber bake converte procedurais antigos), edição via viber scene, scripts Luau (API viber.*). Viber world authoring, declarative XML, Luau scripting.
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

## Regra de ouro: só há mundo estático

- O runtime (e o ViberEditor) só carrega `<world static="true">`. O `world.xml` (+ os
  includes, cada um marcado) **é** a fonte da verdade; os dados binários vivem em
  `world.data/` (`<stem>.data/`): `terrain.ahgt` (+ `.paint`), `veg/NNN.vinst`, `bake.json`.
- Sem geração em runtime: `<StaticSpawner>`, `place`, `<Vegetation>` sem `instances` e
  `<Lake|River rocks="1">` num estático são **ERRO de parse** — usar as ferramentas
  (`viber tool scatter|settle`, `viber scene drop`, pincéis de terreno). As poses do XML
  são finais. Só o `<DynamicSpawner>` (criaturas = gameplay) corre em runtime.
- `viber create` já gera um mundo estático (terreno plano editável).
- `viber bake velho.xml` = conversão ÚNICA, no sítio, de um mundo procedural antigo
  (reescreve os mesmos ficheiros + `<stem>.data/`; recusa um mundo já estático; nunca
  reescreve includes que já são estáticos; os comentários XML perdem-se).

## Fluxo de trabalho do agente

```bash
viber create <nome>            # scaffold estático <nome>/{world.xml,config.yaml,world.data/}
# editar com viber scene/tool/terrain (contrato das tags em references/xml-contract.md)
viber analyze <world.xml>      # valida headless: assets, conflitos, tags — exit 1 em erro
viber analyze <world.xml> --json   # o mesmo relatório num objeto JSON estável (agentes/CI)
viber scene --world w.xml lint [--strict]   # sintaxe Luau de TODOS os scripts, viber.load, quest schema, nomes duplicados
viber scene ls                 # árvore com seletores
viber scene set name=well translation="4 0 3"           # edição offline
viber scene --live set name=well translation="10 8 25"  # engine a correr (undo/redo)
```

Edição = operações atómicas (`src/scene/ops.rs`): `set`/`replace_attrs`/`set_text`/
`insert`/`remove`/`move`/`duplicate` + `terrain_brush`/`terrain_patch`/`terrain_paint`/
`splat_patch`, em lotes (`scene apply ops.json`), cada uma com inversa (a saída traz
`inverse` = o undo offline). Seletores: `world/village.xml#3/0` (ficheiro+path) ou
`name=well` (único; ambíguo = erro com os caminhos). Pose escrita sempre canónica:
`translation`/`euler` (graus XYZ)/`scale`, 4 casas; perto de yaw ±90° com inclinação, onde o
euler não é estável, `rotation` em quaternião.

```bash
viber scene catalog                                   # tags do "Add Node" com snippets válidos
viber scene add PointLight --parent name=plaza        # insere o snippet do catálogo
viber scene drop /assets/meshes/props/barrel.glb --at "12 40 -3" --parent name=city
#   --at é MUNDO: convertido para o referencial do pai (exato num estático)
viber scene files --depth 3                           # a pasta do mundo (o dock FileSystem)
viber scene schema name=bram                          # props --@export do script + valores efetivos
viber scene set name=bram prop-speed=0.8 prop-banter="Olá|Adeus"   # afinar a instância (sem tocar no .lua)
viber scene set name=chest connect-opened="@discordia:notice"      # quem ouve o sinal `opened`
viber scene groups discordia                          # membros do grupo
viber scene extract name=forja --to scenes/forja.xml  # ramo → cena reutilizável
viber scene instance scenes/forja.xml --at "120 0 -40"

viber terrain sample -- -70 -70                       # altura do heightmap (documento)
viber terrain brush raise --at "-70 -70" --radius 25 --amount 4   # raise|lower|flatten|smooth|crater|noise
viber terrain paint --at "10 10" --radius 6 --layer dirt_trail   # camada do pool (splat)

viber tool list                                       # geradores <Scatter id> E <Vegetation instances> do mundo
viber tool scatter id=grp-3                           # regenera o grupo generated-by (determinístico)
viber tool scatter 'ficheiro.xml#i/j'                 # idem numa <Vegetation>: reescreve o .vinst (binário, SEM undo — git/re-scatter)
viber tool settle generated-by=grp-3                  # assenta props no terreno atual (após pincéis)
viber tool canonicalize                               # poses na forma canónica (idempotente)
```

Todos aceitam `--dry-run` (offline) e `--live [--port P]` (engine a correr ou o
editor aberto com `vibereditor --bridge P`: undo partilhado, sync ao vivo). Depois de
mexer no terreno, props/árvores NÃO se re-assentam sozinhos — `tool settle`.

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
- **Props exportadas (`@export` do Godot)**: o script declara
  `--@export speed: number(0..4) = 1.1 -- ajuda` (tipos `number`, `int`, `bool`, `string`,
  `string[]` com `a|b`, `enum(a,b)`, `color`, `vec3`, `entity`); cada instância guarda o
  valor em `prop-<nome>` no XML e o script lê `viber.prop("speed")` no `on_update` (o
  top-level recusa). Para diferenciar NPCs/baús/interações, **exporta e define props** —
  não copies o script. `scene schema` mostra o esquema; `scene lint` apanha
  `script-prop-unknown|type|entity|orphan`; `--live` muda a instância sem respawn.
- **Sinais + grupos (Godot)**: `--@signal opened(gold)` + `viber.emit("opened", g)` no
  emissor; quem ouve é DADO do emissor — `connect-opened="@grupo:metodo; nome:metodo; self:m"`
  (método = função global do script do alvo). `groups="a b"` em qualquer entidade;
  `viber.call_group(g, "m", …)`, `viber.group(g)`, `viber.in_group(g)`, `viber.sender()`.
  Para "quando X acontece, Y reage", **liga por `connect-*`** em vez de procurar entidades por
  nome no código. `scene schema` lista `signals`/`methods`/`connections` (`nodes: 0` = alvo
  partido); `scene groups [g]`; lint `connect-syntax|orphan|signal|target|method`, `script-emit`.
- **Cenas reutilizáveis**: `viber scene extract SEL [--to scenes/x.xml]` ("Salvar ramo como
  cena": o ramo vai para um ficheiro, fica `<Include>` com a pose) e `viber scene instance
  scenes/x.xml --at "x y z"` (mais instâncias; editar o ficheiro muda todas). Undo offline =
  aplicar o `inverse` (apaga a cena criada).
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
