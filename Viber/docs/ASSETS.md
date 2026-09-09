# Assets — o pool único

**Regra:** assets partilhados vivem SÓ em `examples/shared-assets/public/assets/`.
Nenhum exemplo, mundo ou pasta de QA guarda cópias — nem ficheiras duplicadas,
nem symlinks. Este contrato já foi corrigido e reintroduzido duas vezes; a
terceira vez parte a build (ver "O guarda" em baixo).

## O `config.yaml` — o contrato de paths do jogo com a engine

Todo jogo (a pasta que contém o `world.xml`) **tem de ter** um `config.yaml`
ao lado — `viber run`/`analyze` não arrancam sem ele (`viber create` gera um
completo). É dele que a engine tira TODOS os diretórios: a engine não tem
layouts de filesystem hardcoded, nem descobre pools por conta própria.

```yaml
title: Simple RPG              # opcional — título da janela

assets:                        # resolve contra as ROOTS (AssetServer)
  roots:                       # extra, por ordem, DEPOIS da pasta do jogo
    - ../shared-assets/public
  bgm_dir: assets/audio/bgm              # <MusicLayer layer="x"> → {bgm_dir}/x.ogg
  sfx_dir: assets/audio/sfx              # clips da engine → {sfx_dir}/{clip}
  terrain_textures_dir: assets/textures  # layer de terreno → {dir}/{alias}/{albedo,normal}.ktx2

game:                          # resolve contra a PASTA DO JOGO (filesystem)
  scripts_dir: scripts         # script="x.lua" + hot-reload

save:
  dir: ~/.local/share/viber    # absoluto (~ ok); {dir}/{mundo}.save.json
```

## Como a engine resolve um caminho

Toda a referência a assets nos XML (`url=`, `texture=`, `model-url=`,
`heightmap=`, `meshes=`, ícones `<UiIcon src>`, áudio por convenção) resolve
contra a **lista de asset roots por ordem de precedência**:

1. **A pasta do jogo** — SEMPRE a 1.ª (implícita): é onde vivem os
   **overrides por-mundo** (um ficheiro local ganha ao pool) e os
   **shaders especializados** (`shaders/{sky,water,terrain_chunk}.wgsl`,
   escritos no arranque — as roots extra nunca são escritas).
2. **As roots extra do `assets.roots`** do config, por ordem — tipicamente
   o pool partilhado.

`VIBER_ASSET_POOL` (caminho; `"0"` desliga) continua a existir como override
de debug/CI e ganha às roots do config. Implementação:
`MeshoptAssetReader` (decode EXT_meshopt à leitura) envolve o
`MultiRootFileReader` (primeira root que tem o ficheiro ganha) —
`meshopt::register_asset_source(app, roots)` em `main.rs::run`. O `analyze`
usa a mesma lista (`world_asset_dirs` + `audit::resolve_asset`), e o
heightmap segue a mesma ordem em `terrain::runtime::load_heightmap`.
Consequência prática: **os XML ficam sempre com `/assets/…`** — não há
esquema `/pool/…` nem `../`; o que decide quem serve é o config do jogo.

### Bases de resolução (não misturar)

- `assets.*` — paths de AssetServer, tentados em cada root por ordem.
- `game.*` — filesystem direto contra a pasta do jogo (`UiStyle src="@ui/hud.css"`
  também: o prefixo `ui/` é parte do caminho autor — contrato do XML, não um
  dir do config; juntar um `ui_dir` duplicava o prefixo e despia o HUD de
  estilo, caso real 2026-09-08).
- `save.dir` — absoluto (`~` expandido no load).

### Contrato de CONTEÚDO (o que o config NÃO remapeia)

O config remapeia DIRETÓRIOS; os nomes de ficheiro dentro deles são o
contrato de conteúdo do pool, igual para todos os jogos: o nome de cada clip
SFX (`combat/swing.ogg`, registry em `ambient.rs`), `{layer}.ogg` para BGM,
`{alias}/albedo.ktx2`|`normal.ktx2` para as 13 layers de terreno
(`splat::DEFAULT_LAYERS`), os 12 GLBs de shore rocks, e o diretório
`shaders/` da 1.ª root (os `Material::fragment_shader()` do Bevy são fns
ESTÁTICAS que leem `shaders/*.wgsl` contra as roots — remapear a escrita sem
a leitura partia o render; por isso NÃO há `shaders_dir` no schema).

## O que vive onde

| Conteúdo | Sítio | Versionado |
|---|---|---|
| Assets partilhados (meshes, texturas de terreno, áudio, ícones comuns) | `examples/shared-assets/public/assets/` | pool: o que as regras do pool versionam (ver README do pool) |
| Por-jogo (ícones HUD do exemplo, heightmap próprio, `world/`, scripts) | `<exemplo>/assets/`, `<exemplo>/` | **SIM, sempre** — conteúdo vivo nunca vive só numa árvore descartável |
| Shaders especializados por mundo | `<mundo>/shaders/` (escritos no arranque) | não (gerados) |

## História (para não repetir)

- **2026-08-25** — VibeGame elimina o distribuidor por exemplo: plugin vite
  `sharedAssets` (`0ff912e6`, `e642c7cb`) — "um pool, sem symlinks nem
  cópias". O `public/` do exemplo responde primeiro, o pool serve o resto.
- **2026-09-01** — O Viber reintroduz cópias **por design**: o Bevy 0.19 não
  lia meshopt/KTX2/quantização, e `scripts/sync_assets.py` passou a gerar um
  **espelho** decomprimido por exemplo (`examples/simple-rpg/assets/`,
  gitignored, ~378 MB). Entretanto 4 WAVs chegaram a ser commitados DENTRO
  do espelho (`4a11c133`) — corrigidos em `b43cb2c0`: assets vivem no pool.
- **2026-09-07** — `MeshoptAssetReader` (`b4bd0980`) já descomprimia à
  leitura; o espelho deixou de ter razão de ser. A migração apagou o
  espelho, o `sync_assets.py` e o symlink `worlds/assets`, e deu à engine o
  fallback multi-root (a mesma semântica do plugin vite, nativa).
- **2026-09-08** — `config.yaml` obrigatório por jogo: TODOS os diretórios
  (roots extra, bgm/sfx, texturas de terreno, scripts, UI, saves) passam a
  vir do config; a engine deixou de saber onde vive o pool (o walk de
  ancestros `examples/shared-assets/public` saiu do código —
  `meshopt::shared_asset_pool()` sobrevive só como helper de testes, para
  quem a localização do pool é facto do checkout).

## O guarda

`tests/asset_pool_dedup.rs` parte a build se:

- algum `examples/*/assets/**` repetir um caminho que existe no pool (shadow)
  sem estar na allowlist do teste com justificação — é o espelho a voltar,
  seja por script ressuscitado, seja por cópia manual;
- `worlds/assets` voltar a existir (foi symlink para o pool; symlinks estão
  banidos — quebram ferramentas que não os seguem).

Override por-mundo é legítimo (a root do mundo ganha), mas cada ficheiro em
shadow tem de estar na allowlist do guarda com um comentário a dizer porquê.

## Adicionar assets novos

1. Partilhado → gerar para dentro do pool (`examples/shared-assets/`, ver o
   README do pool: manifests, `gameassets resume`, KTX2/UASTC obrigatório —
   nunca etc1s/BasisLZ; `docs/PERFORMANCE.md`). Os jogos que referenciam o
   asset nem mexem no config — as roots já apontam ao pool.
2. Por-jogo → `<jogo>/assets/…` (a pasta do jogo é a 1.ª root: ganha ao
   pool), **commitado**.
3. Referenciar sempre `/assets/…` no XML; `viber analyze <mundo>.xml`
   confirma que resolve contra as roots do config do jogo.

## Nota ao trabalho de enrich (WIP)

O passe de materiais (`scripts/enrich_materials.py`, não versionado) produziu
`meshes_enriched/` na (já extinta) árvore local do simple-rpg; o mundo QA que
o referenciava (`worlds/qa-enriched.xml`) também foi removido. Quando esse
trabalho aterrar de novo: os outputs vão para o POOL (partilhados) ou para
`<jogo>/assets/` versionado (por-jogo) — nunca para uma árvore descartável,
e o `--src` do script aponta ao pool.
