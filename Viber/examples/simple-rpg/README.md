# simple-rpg (migrado para Viber)

Porta do exemplo original (motor TS) para a engine nativa Viber.
O mundo foi convertido uma única vez por um script de migração (já removido).
O jogo
corre de ponta a ponta: as Fases 0–3 da engine estão feitas e as tags do
mundo original já têm runtime, incluindo as tags `EngineConfig`
(`NavMesh`, `SpawnGate`, `ProjectileTemplate`, `AdaptiveQuality`,
`PostFxDebugToggle`).

## Só estático

`world.xml` + `world/**` + `world.data/` **são o mundo** — estático
(`static="true"`), a fonte da verdade: terreno final pré-gerado
(`world.data/terrain.ahgt`), geradores resolvidos em grupos `spawn/…`
editáveis (receita `<Scatter>` ao lado), vegetação em `.vinst`. É o que corre
e o que se edita (agentes e ViberEditor, com paridade total via
`viber::scene`). Não há fonte procedural: foi convertida no sítio e apagada
(2026-10-14; fica no histórico do git).

Regras de edição e QA: [`AGENTS.md`](AGENTS.md). Contrato completo:
`Viber/AGENTS.md`, secção "Mundos estáticos + edição por agentes".

## Correr

```bash
cd Viber
cargo run -- run examples/simple-rpg/world.xml
cargo run -- analyze examples/simple-rpg/world.xml   # headless + cobertura
```

(ou, com o CLI instalado e o cwd NA pasta do mundo: `viber run` — a
descoberta procura `world.xml` → `worlds/*.xml`)

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

**Atiradores e elites:** os bandidos do ermo oriental (`enemies/bandit-archer.lua`)
mantêm 5–15 m e disparam o `enemy-arrow` (anel laranja = vai disparar; sai da
linha). Qualquer criatura não-chefe tem 8 % de nascer **elite** — maior, 2,5× a
vida, golpes ×1,4, aura dourada ao acordar — e paga a triplicar (`game/loot.lua`).

**Guia de missões:** o NPC com missão tem "!" (por aceitar) ou "?" (por
entregar) a flutuar e vira-se para o herói; a seta do HUD (`<WaypointArrow>`)
aponta entregar → cumprir (marco por visitar / presa viva mais próxima) →
missão nova. Minimapa e bússola só mostram NPC que valem a pena.

**Exploração:** baús escondidos junto a seis marcos (caravana, barco, menires,
altar, obelisco, mojão) com loot próprio (`prop-*`).

As teclas de debug ([H] dano, [N] cura total, [K] +XP, F6/F7/F9/F10/F11
teleportes e recursos) só existem com `VIBER_DEBUG_KEYS=1`.

Assets: `/assets/…` resolve contra as asset roots do `config.yaml` deste
jogo — a pasta do jogo SEMPRE primeiro (overrides), o pool partilhado
`Viber/examples/shared-assets/public/assets` declarado em
`assets.roots` como root extra (`Viber/docs/ASSETS.md`). A engine
descomprime meshopt/KTX2 à leitura (`src/meshopt.rs`), portanto **não há
espelho nem cópia local do pool** (guarda anti-regressão:
`tests/asset_pool_dedup.rs`).

## Estrutura

- `world.xml` — **raiz do mundo (estático, fonte da verdade)**:
  `<world static="true">`, `<Include src="/world/…">`, terreno no heightmap
  final `world.data/terrain.ahgt`
- `world/**.xml` — módulos do mundo (um ficheiro por include, cada um marcado
  `static="true"`): cidades, criaturas, landmarks, `spawn/…` (saída dos
  geradores), vegetação com `instances="world.data/veg/NNN.vinst"`, interiores
- `world.data/` — dados binários: `terrain.ahgt` (+ `.paint`), `veg/*.vinst`,
  `bake.json` (documento da conversão — World Overview do editor)
- `quests/*.json` — definições das 27 quests (lidas do disco pela engine via
  `game.quests_dir`; campos: `id, npc, biome, title, lines_*, objective, rewards`)
- `scripts/**.lua` — comportamento Luau (inimigos/bosses, colheita, POIs,
  HUD/UI) via a API `viber.*` (`Viber/docs/LUA_API.md`)
- `config.yaml` — **contrato de paths com a engine** (obrigatório; roots,
  dirs de áudio/texturas/scripts/UI, save) — `Viber/docs/ASSETS.md`
- `ui/*.css` + `world/hud.xml`/`world/menu.xml` — HUD e menu declarativos
- `qa-*.xml` / `ui-showcase.xml` — mundos QA, também estáticos (incluem
  módulos partilhados de `world/**`)

## Editar

Por operações — `viber scene` / `viber tool` / `viber terrain` (offline ou
`--live`) ou o ViberEditor. Re-gerar a partir do exemplo original do motor TS
deixou de ser o fluxo: produz um
mundo procedural que teria de ser convertido (`viber bake`) e substituiria o
estático editado. Depois de mudanças nos assets (GLBs novos): os partilhados
vão para o POOL (`Viber/examples/shared-assets/`), os por-jogo para
`assets/` versionado; `viber analyze` confirma que tudo resolve.

## Fila aberta (na engine, não neste mundo)

- Instancing GPU para vegetação (hoje cap 800 instâncias/tag)
