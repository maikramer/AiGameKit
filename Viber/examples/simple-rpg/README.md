# simple-rpg (migrado para Viber)

Porta do exemplo `VibeGame/examples/simple-rpg` para a engine nativa Viber.
O mundo foi convertido por `Viber/scripts/migrate_from_vibegame.py`. O jogo
corre de ponta a ponta: as Fases 0–3 da engine estão feitas e as tags do
mundo original já têm runtime, incluindo as tags `EngineConfig`
(`NavMesh`, `SpawnGate`, `ProjectileTemplate`, `AdaptiveQuality`,
`PostFxDebugToggle`).

## Estático-first

O mundo vive em dois estados com papéis distintos:

- **`world.static.xml` + `static/**` + `bake/**` são a fonte da verdade** — o
  mundo estático cozido por `viber bake` (terreno carvado pré-gerado,
  spawners resolvidos em grupos `spawn/…` editáveis, vegetação em
  `.vinst`). É o que corre e o que se edita (agentes e ViberEditor, com
  paridade total via `viber::scene`).
- **`world.xml` + `world/**` são a fonte procedural** — servem para
  regenerar o estático de raiz (`viber bake world.xml --force`, que DESCARTA
  as edições do estático) e para auditar o gerador (`viber analyze
  world.xml`).

Regras de edição e QA: [`AGENTS.md`](AGENTS.md). Contrato completo:
`Viber/AGENTS.md`, secção "Mundos estáticos + edição por agentes".

## Correr

```bash
cd Viber
cargo run -- run examples/simple-rpg/world.static.xml     # o estático (verdade)
cargo run -- analyze examples/simple-rpg/world.static.xml # headless + cobertura
cargo run -- run examples/simple-rpg/world.xml            # o PROCEDURAL, explícito
```

(ou, com o CLI instalado e o cwd NA pasta do mundo: `viber run` — a
descoberta prefere `world.static.xml` → `world.xml` → `worlds/*.xml`)

## Estado atual

Jogável de ponta a ponta: **player** com combate melee ([J]/clique, alvo [V],
dash [C], golpe radial [R], bomba [B], guard [L], talentos com [Q]→Talentos),
**27 quests** (JSON em `quests/`, diálogo [E] com os NPCs, tracker no HUD,
bounties no notice-board), **economia** (vault gold/wood/stone, colheita de
árvores/pedras, loja no diário [Q]→Loja — falar com o Bram [E] abre-a —,
hotbar [1]/[2], despojos de ouro/poções nos abates), **travel** (A Nota —
12 marcos para assinar com [F], viagem rápida [G] nas fogueiras, seta de
waypoint), **save/load** (menu [Q]→Sistema: [J] grava, [L] carrega; JSON em
`~/.local/share/viber/`), **mundo vivo** (céu procedural + ciclo dia/noite,
clima, fog/tint por BiomeRegion, BGM com crossfade por zona, SFX, emissores
de partícula) e **física** (character controller cinemático, knockback,
destrutíveis que tombam). Os ~46 scripts Luau em `scripts/` (inimigos,
bosses, colheita, POIs, HUD) correm via a API `viber.*` com "LOD de IA" —
além do raio de ativação (default 45 m) o `on_update` nem corre — e têm
hot-reload ao gravar. A referência da API está em `Viber/docs/LUA_API.md`.

O `analyze` imprime o relatório de cobertura — é o roteiro do que falta à
engine.

### Combate (lib/fsm.lua)

Os golpes inimigos são **legíveis**: a criatura trava, toca o clip `attack`
e pulsa um anel âmbar no chão durante o *windup*; o dano só entra se o herói
ainda estiver ao alcance no impacto — recuar, dash [C] ou aparar [L] no
tempo certo resolvem (sai "ESQUIVA"). Depois do golpe há uma janela de
punição (*recover*). Atingir uma criatura fá-la vacilar (clip `hit`) e
cancela o windup. Os **chefes** (Bruxa, Verme, Guardião do Lodo, Ogro) têm
barra própria no HUD, *poise* (só vacilam com dano acumulado), um especial a
cada 3 golpes com anel de aviso colorido (a maldição da Bruxa cai onde o herói
estava) e fúria abaixo de ~40 % de vida. Cada um tem dador de quest.

As teclas de debug de vitais ([H] dano, [N] cura total, [K] +XP) só existem
com `VIBER_DEBUG_KEYS=1`.

Assets: `/assets/…` resolve contra as asset roots do `config.yaml` deste
jogo — a pasta do jogo SEMPRE primeiro (overrides), o pool partilhado
`Viber/examples/shared-assets/public/assets` declarado em
`assets.roots` como root extra (`Viber/docs/ASSETS.md`). A engine
descomprime meshopt/KTX2 à leitura (`src/meshopt.rs`), portanto **não há
espelho nem cópia local do pool** (guarda anti-regressão:
`tests/asset_pool_dedup.rs`).

## Estrutura

- `world.static.xml` — **raiz do mundo estático (fonte da verdade)**:
  `<world static="true">`, includes espelhados em `static/**`, terreno no
  heightmap pré-carvado `bake/terrain.baked.ahgt`
- `static/**.xml` — módulos do estático (um ficheiro por include): cidades,
  criaturas, landmarks, `spawn/…` (saída dos geradores), vegetação com
  `instances="bake/veg/NNN.vinst"`
- `bake/` — artefactos do bake: `terrain.baked.ahgt` (heightmap carvado),
  `bake.json` (manifesto), `veg/*.vinst` (vegetação pré-gerada)
- `world.xml` + `world/**.xml` — fonte PROCEDURAL (gerador; só para
  `viber bake --force`)
- `quests/*.json` — definições das 27 quests (lidas do disco pela engine via
  `game.quests_dir`; campos: `id, npc, biome, title, lines_*, objective, rewards`)
- `scripts/**.lua` — comportamento Luau (inimigos/bosses, colheita, POIs,
  HUD/UI) via a API `viber.*` (`Viber/docs/LUA_API.md`)
- `config.yaml` — **contrato de paths com a engine** (obrigatório; roots,
  dirs de áudio/texturas/scripts/UI, save) — `Viber/docs/ASSETS.md`
- `ui/*.css` + `world/hud.xml`/`world/menu.xml` — HUD e menu declarativos
- `tools/` — scripts de geração por-jogo (ex.: `gen_heightmap.py`)
- `qa-*.xml` / `ui-showcase.xml` — mundos QA pequenos, procedurais de
  propósito (dispensam bake)

## Re-migrar / re-bakear

Depois de mudanças no mundo original (ou no conversor):

```bash
python3 Viber/scripts/migrate_from_vibegame.py \
  VibeGame/examples/simple-rpg/index.html \
  --public Viber/examples/shared-assets/public \
  -o Viber/examples/simple-rpg/
viber bake Viber/examples/simple-rpg/world.xml --force   # re-gera o estático (perde edições)
```

A re-migração reescreve a fonte PROCEDURAL; o estático só acompanha com um
`viber bake --force`, que descarta as edições feitas no `world.static.xml`/
`static/**` desde o último bake — coordenar as duas coisas.

Depois de mudanças nos assets (novos refs, GLBs novos): os assets
partilhados vão para o POOL (`Viber/examples/shared-assets/` — ver o README
do pool); por-jogo ficam em `assets/` versionado. `viber analyze`
confirma que tudo resolve.

Cada ficheiro de saída leva um cabeçalho com os attrs descartados e as tags
passadas verbatim. Regras de mapeamento: docstring do conversor.

## Fila aberta (na engine, não neste mundo)

- Instancing GPU para vegetação (hoje cap 800 instâncias/tag)
