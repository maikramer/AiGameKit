# simple-rpg (migrado para Viber)

Porta do exemplo `VibeGame/examples/simple-rpg` para a engine nativa Viber.
O mundo foi convertido por `Viber/scripts/migrate_from_vibegame.py`. O jogo
corre de ponta a ponta: as Fases 0–3 da engine estão feitas e as tags do
mundo original já têm runtime — só as tags `EngineConfig` data-only
(`NavMesh`, `SpawnGate`, `ProjectileTemplate`, `AdaptiveQuality`,
`PostFxDebugToggle`) passam verbatim sem consumidor.

## Correr

```bash
cd Viber
cargo run -- run examples/simple-rpg/world.xml          # janela
cargo run -- analyze examples/simple-rpg/world.xml       # headless + cobertura
```

(ou, com o CLI instalado: `viber run examples/simple-rpg/world.xml`)

## Estado atual

Jogável de ponta a ponta: **player** com combate melee ([J]/clique, alvo [V],
dash [C], golpe radial [R], bomba [B], guard [L], talentos com [Q]→Talentos),
**21 quests** (JSON em `quests/`, diálogo [E] com os NPCs, tracker no HUD,
bounties no notice-board), **economia** (vault gold/wood/stone, colheita de
árvores/pedras, loja [K] no mercador, hotbar [1]/[2]), **travel** (A Nota —
12 marcos para assinar com [F], viagem rápida [G] nas fogueiras, seta de
waypoint), **save/load** (menu [Q]→Sistema: [J] grava, [L] carrega; JSON em
`~/.local/share/viber/`), **mundo vivo** (céu procedural + ciclo dia/noite,
clima, fog/tint por BiomeRegion, BGM com crossfade por zona, SFX, emissores
de partícula) e **física** (character controller cinemático, knockback,
destrutíveis que tombam). Os ~38 scripts Luau em `scripts/` (inimigos, bosses,
colheita, POIs, HUD) correm via a API `viber.*` com "LOD de IA" — além do
raio de ativação (default 45 m) o `on_update` nem corre. A referência da API
está em `Viber/docs/LUA_API.md`.

O `analyze` imprime o relatório de cobertura — é o roteiro do que falta à
engine (hoje: só as 5 tags `EngineConfig` data-only).

Assets: `/assets/…` resolve contra as asset roots do `config.yaml` deste
jogo — a pasta do jogo SEMPRE primeiro (overrides), o pool partilhado
`Viber/examples/shared-assets/public/assets` declarado em
`assets.roots` como root extra (`Viber/docs/ASSETS.md`). A engine
descomprime meshopt/KTX2 à leitura (`src/meshopt.rs`), portanto **não há
espelho nem cópia local do pool** (guarda anti-regressão:
`tests/asset_pool_dedup.rs`).

## Estrutura

- `world.xml` — raiz ( porta do `index.html` original; `<Scene>` → `<world>` ),
  com a UI declarativa (`UiRoot`/`UiStyle`) de tabs do HUD
- `world/**.xml` — módulos migrados, espelham `public/world/` do original
- `quests/*.json` — definições das 21 quests (embutidas na engine via
  `include_str!`; campos: `id, npc, biome, title, lines_*, objective, rewards`)
- `scripts/**.lua` — comportamento Luau (inimigos/bosses, colheita, POIs,
  HUD/UI) via a API `viber.*` (`Viber/docs/LUA_API.md`)
- `config.yaml` — **contrato de paths com a engine** (obrigatório; roots,
  dirs de áudio/texturas/scripts/UI, save) — `Viber/docs/ASSETS.md`
- `tools/` — scripts de geração por-jogo (ex.: `gen_heightmap.py`)

## Re-migrar

Depois de mudanças no mundo original (ou no conversor):

```bash
python3 Viber/scripts/migrate_from_vibegame.py \
  VibeGame/examples/simple-rpg/index.html \
  --public Viber/examples/shared-assets/public \
  -o Viber/examples/simple-rpg/
```

Depois de mudanças nos assets (novos refs, GLBs novos): os assets
partilhados vão para o POOL (`Viber/examples/shared-assets/` — ver o README
do pool); por-jogo ficam em `assets/` versionado. `viber analyze
examples/simple-rpg/world.xml` confirma que tudo resolve.

Cada ficheiro de saída leva um cabeçalho com os attrs descartados e as tags
passadas verbatim. Regras de mapeamento: docstring do conversor.

## Fila aberta (na engine, não neste mundo)

- Consumir as tags `EngineConfig` data-only: `NavMesh`, `SpawnGate`,
  `ProjectileTemplate`, `AdaptiveQuality`, `PostFxDebugToggle`
- Hot-reload de scripts Luau
- Nametags de HUD (sistema comentado — `BISECT` em `Viber/src/main.rs`)
- Instancing GPU para vegetação (hoje cap 800 instâncias/tag)
