# AGENTS.md — examples/simple-rpg

Mundo de referência jogável (port do exemplo original, motor TS).
Panorama e estado: [`README.md`](README.md). Aqui ficam as regras para editar.

## Só estático (LER PRIMEIRO)

O mundo é **estático e é a fonte da verdade**: `world.xml` (`<world static="true">`) +
`world/**` (cada include também marcado) + `world.data/` (dados binários). É o que
`viber run`/`scene`/`analyze`/`tool` e o ViberEditor abrem e editam. Não há fonte
procedural nem geradores em scripts: o antigo `world.xml` procedural, os `world/**`
procedurais e `tools/*.py` (gen_heightmap/gen_interiors/height_probe) foram apagados em
2026-10-14 — ficam no histórico do git. As poses do XML são FINAIS: o runtime não
assenta, não coloca (`place`) e não gera nada (spawners/`place` num estático são erro de
parse). Única geração em runtime: `<DynamicSpawner>` (criaturas = gameplay).

Contrato completo (ops, ferramentas, paridade editor/CLI): `Viber/AGENTS.md`, secção
"Mundos estáticos + edição por agentes".

## Mapa

| Caminho | O que é |
|---------|---------|
| `world.xml` | **o mundo (estático, verdade)** — raiz com `static="true"` e os `<Include src="/world/…">` |
| `world/**.xml` | módulos do mundo (um ficheiro por include): cidades, criaturas, landmarks, vegetação com `instances=`, grupos `spawn/…` gerados por `<Scatter>`, interiores, HUD/menu |
| `world.data/terrain.ahgt` | heightmap FINAL (lagos/rios/estradas/pads já carvados) — o `<Terrain heightmap>` aponta para cá; editável com pincéis (`viber terrain brush|paint`, editor B); a pintura vive no sidecar `terrain.ahgt.paint` |
| `world.data/veg/*.vinst` | vegetação pré-gerada — `<Vegetation instances="world.data/veg/NNN.vinst">` carrega as instâncias sem calcular nada |
| `world.data/bake.json` | documento da conversão (terreno, features resolvidas, geradores) — o World Overview do editor |
| `quests/*.json` | definições das 27 quests — lidas do DISCO no arranque (reiniciar a engine; sem `cargo build`) |
| `scripts/**.lua` | comportamento (inimigos/bosses, colheita, POIs, HUD/UI) — hot-reload ao gravar (`src/hot_reload.rs`; `VIBER_HOT_RELOAD=0` desliga) |
| `ui/*.css` + `world/hud.xml` / `world/menu.xml` | HUD e menu [Q] declarativos |
| `shaders/*.wesl` | céu/terreno/água — **reescritos em disco a cada `run`** (especializados pelos attrs); não editar à mão expectando persistência |
| `qa-*.xml`, `ui-showcase.xml` | mundos QA — também estáticos; incluem módulos de `world/**` (partilhados: editar lá muda o mundo principal). `qa-cenarios`/`qa-fantasia` usam o MESMO `world.data/terrain.ahgt`; os pequenos têm `<stem>.data/` próprio |

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
  desfazível. Os `<Scatter id="engine-N">` da raiz do `world.xml` são as
  pedras de margem dos lagos — também regeneráveis pela mesma tool. As
  `<Vegetation instances="world.data/veg/NNN.vinst">` regeneram com
  `viber tool scatter ficheiro#i/j` (reescreve o `.vinst` atómico; binário,
  NÃO desfazível via History — git ou re-scatter).
- **Pinceladas de terreno/pintura gravam em `world.data/`**: `terrain.ahgt`
  + `terrain.ahgt.paint` são PARTE DO ESTADO DO MUNDO — commita-os como o
  resto (não estão no .gitignore de propósito).
- **Interiores são CENAS** (`config.yaml` → `project:`): cada sala é
  `scenes/interiors/<sala>.xml` (universo próprio, sem terreno), com
  `<SceneEntry name="from-town">` e `<ScenePortal name="exit" target="main"
  entry="@back" auto-radius="1.6">`. As portas da vila são `<ScenePortal
  target="interiors/<sala>" entry="from-town">` em `world/cities/discordia/portals.xml`
  (as 3 bancas do mercado apontam para a mesma sala; `@back` devolve à banca usada).
  Jogador, câmara e HUD são autoloads (`world/autoload/*.xml`). Sala nova:
  `viber scene new-scene interiors/<nome> --interior "20 16"` + um `<ScenePortal>` na
  vila; `viber scene lint` valida alvos e entradas. Editar uma sala: abrir a aba no
  editor (dock Ficheiros) ou `viber scene --world scenes/interiors/<sala>.xml …`.
- Depois de qualquer edição: `viber analyze` (exit 1 em erro; warnings listam
  attrs desconhecidos; `<StaticSpawner>`/`place` num estático = erro).
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
viber run                                    # descobre world.xml
viber run examples/simple-rpg/world.xml      # explícito
viber run --bridge                           # QA: bridge BRP (porta 15702)
```

QA vivo: plugin **viber-live-qa** (skill + command `/viber-qa`) — sessão
partilhada (`viber session up/claim/release`), `viber debug
screenshot/tree/prof/lua`, edição `viber scene --live`. Uma engine de cada
vez (VRAM).

## Verificar

```bash
cd Viber && cargo test
cargo run -- analyze examples/simple-rpg/world.xml
```
