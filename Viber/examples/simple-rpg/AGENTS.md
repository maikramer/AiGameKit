# AGENTS.md — examples/simple-rpg

Mundo de referência jogável (port do `VibeGame/examples/simple-rpg`).
Panorama e estado: [`README.md`](README.md). Aqui ficam as regras para editar.

## Estático-first (LER PRIMEIRO)

O mundo tem DOIS estados, com papéis distintos:

- **`world.static.xml` + `static/**` + `bake/**` são a FONTE DA VERDADE** — é o
  mundo estático que `viber run`/`scene`/`analyze` abrem por omissão
  (auto-descoberta prefere `world.static.xml`) e o que agentes e ViberEditor
  editam. As poses do XML são FINAIS: em `<world static="true">` o runtime não
  assenta (`SeatOnTerrain`) nem coloca (`place`) nada.
- **`world.xml` + `world/**` são a FONTE PROCEDURAL** — só servem para
  regenerar o estático de raiz: `viber bake world.xml --force`. O `--force`
  **DESCARTA todas as edições** feitas no `world.static.xml`/`static/**` desde
  o último bake. Nunca re-bakear para "aplicar" uma edição pontual.

Contrato completo (bake, ops, paridade editor/CLI): `Viber/AGENTS.md`, secção
"Mundos estáticos + edição por agentes".

## Mapa

| Caminho | O que é |
|---------|---------|
| `world.static.xml` | **mundo estático (verdade)** — raiz com `static="true"`, includes apontam a `static/**` |
| `static/**.xml` | módulos do estático (um ficheiro por include do `world.xml` original): cidades, criaturas, landmarks, vegetação com `instances=`, grupos `spawn/…` |
| `bake/terrain.baked.ahgt` | heightmap CARVADO pelo bake (lagos/rios/estradas/pads já aplicados) — o `<Terrain heightmap>` do estático aponta para cá; editável com pincéis `viber terrain` |
| `bake/bake.json` | manifesto do bake (fonte, terreno, camadas, geradores) |
| `bake/veg/*.vinst` | vegetação pré-gerada — `<Vegetation instances="bake/veg/NNN.vinst">` carrega as instâncias sem calcular nada em runtime |
| `world.xml` + `world/**.xml` | fonte PROCEDURAL (gerador) — só para `viber bake --force` |
| `quests/*.json` | definições das 27 quests — lidas do DISCO no arranque (reiniciar a engine; sem `cargo build`) |
| `scripts/**.lua` | comportamento (inimigos/bosses, colheita, POIs, HUD/UI) — hot-reload ao gravar (`src/hot_reload.rs`; `VIBER_HOT_RELOAD=0` desliga) |
| `ui/*.css` + `static/world/hud.xml` / `static/world/menu.xml` | HUD e menu [Q] declarativos (o lado `world/` é só a fonte procedural) |
| `shaders/*.wesl` | céu/terreno/água — **reescritos em disco a cada `run`** (especializados pelos attrs); não editar à mão expectando persistência |
| `qa-*.xml`, `ui-showcase.xml` | mundos QA pequenos — **procedurais de propósito** (dispensam bake) |

## Regras ao editar

- **Editar o estático com operações, nunca à mão "à espera do runtime":**
  `viber scene` (offline) ou `viber scene --live` (engine a correr: grava +
  sync ao vivo + undo/redo), ou o ViberEditor. O seating/settle de props e
  spawners NÃO corre em mundos estáticos — hand-editar poses no XML e esperar
  que a engine as ajuste não funciona (as poses do estático são finais).
  Depois de pincéis de terreno: `viber tool settle SEL` assenta o que precisa.
- **Regenerar conteúdo espalhado** (árvores, pedras, dressing): não re-bakear —
  `viber tool list` / `viber tool scatter id=grp-N [--live]` recalcula só aquele
  gerador sobre o terreno ATUAL e substitui o grupo `spawn/…` numa transação
  desfazível. Os `<Scatter id="engine-N">` da raiz do `world.static.xml` são as
  pedras de margem dos lagos — também regeneráveis pela mesma tool. As
  `<Vegetation instances="bake/veg/NNN.vinst">` regeneram com
  `viber tool scatter ficheiro#i/j` (reescreve o `.vinst` atómico; binário,
  NÃO desfazível via History — git ou re-scatter).
- **Pinceladas de terreno/pintura gravam sidecar**: `bake/terrain.baked.ahgt`
  + `bake/terrain.baked.ahgt.paint` são PARTE DO ESTADO DO MUNDO — commita-os
  como o resto do estático (não estão no .gitignore de propósito).
- **Portais das casas são data-driven**: `scripts/building-portal.lua` deriva
  o registo dos NOMES das entidades (`portal.<sala>[.<sufixo>]` fora,
  `portal.exit_<sala>` dentro da bolsa) — mover/acrecentar portas no editor
  FUNCIONA sem tocar no script. Salas novas: `tools/gen_interiors.py`
  (regenera interiors+portals nos DOIS lados).
- Depois de qualquer edição: `viber analyze` (exit 1 em erro; warnings listam
  attrs desconhecidos; `<StaticSpawner>`/`place` num estático = warning).
- **Gotcha self-closing:** acrescentar filhos a `<Entity … />` cria um
  *irmão* afixado à origem (= spawn do player) — converter para forma aberta.
  O `analyze` **não** apanha isto. (Com `viber scene insert` não há risco.)
- Estradas/lagos/rios/pads têm ordem de carve e limites (ver
  `src/terrain/AGENTS.md`); estradas < ~1.5 texéis viram no-op.
- Assets: NÃO há espelho nem cópias por exemplo — `/assets/…` resolve contra
  as asset roots do `config.yaml` (pasta do jogo primeiro, pool partilhado
  `Viber/examples/shared-assets/public/assets` como root extra; meshopt/KTX2
  descomprimidos à leitura). Guarda: `tests/asset_pool_dedup.rs`.

## Correr / QA

```bash
viber run                                    # descobre world.static.xml primeiro
viber run examples/simple-rpg/world.static.xml   # o estático, explícito
viber run examples/simple-rpg/world.xml      # correr o PROCEDURAL explicitamente
viber run --bridge                           # QA: bridge BRP (porta 15702)
```

QA vivo: plugin **viber-live-qa** (skill + command `/viber-qa`) — sessão
partilhada (`viber session up/claim/release`), `viber debug
screenshot/tree/prof/lua`, edição `viber scene --live`. Uma engine de cada
vez (VRAM).

## Verificar

```bash
cd Viber && cargo test
cargo run -- analyze examples/simple-rpg/world.static.xml   # o estático (verdade)
cargo run -- analyze examples/simple-rpg/world.xml          # auditar o gerador procedural
```
