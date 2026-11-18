# AiGameKit Studio

GUI nativa do pipeline de assets do AiGameKit: um asset por projeto, do
prompt/referência até ao `_lod0.glb` com colisão, com viewport 3D em Bevy e
controlo por etapa.

A app é Rust (Bevy 0.20 + egui). O ArtCraft serviu de referência de UX
(prompt/refs à esquerda, palco 3D ao centro, pipeline à direita, fila em
baixo); não é dependência — é Tauri/React/three.js e os crates internos não
são publicados.

## O que faz

- **Fonte**: prompt (`idea`) e/ou imagem de referência (arrastar para a
  janela). Sem referência, o `text2d` gera-a a partir do prompt.
- **Pipeline**: shape (`text3d`), topology-fix, Hi3D refine opcional,
  orçamento `to_paint`, pintura (`paint3d`), rig (`rigging3d`), animação
  (`animator3d`) e/ou movimento por texto (`motion3d`), LODs e colisão.
- **Preview 3D**: cada artefacto intermédio e final abre no viewport (órbita,
  pan, zoom, enquadrar, wireframe, turntable, clips com play/pause/velocidade).
- **Re-roll por etapa**: guarda o artefacto antigo em `.studio/history/`,
  invalida a jusante e corre só o que falta.
- **Movimento por texto**: `motion3d` (CPU) gera NPZ, `apply-rigged` dá
  preview e `pack-rigged` empacota os clips no `_rigged_animated.glb`.
- **Biblioteca**: grelha de projetos com thumbnail (`cover.png`), busca e
  abertura.

## Requisitos

- Rust ≥ 1.97.1 (Bevy 0.20.1).
- O decoder de meshopt/KTX2 vem vendorizado em `vendor/bevy_ext_gltf`
  (snapshot do crate `bevy_ext_gltf` do irmão `bevy_extensions`; ver
  `vendor/bevy_ext_gltf/VENDORED.md`). Não é preciso o checkout irmão.
- `gameassets` no `PATH` (e as venvs das tools que o perfil usar). O
  `gameassets studio check` devolve o mapa `tools` com o que falta.
- GPU local ou `VRAMD_REMOTE` configurado. O Studio herda o ambiente de onde
  é lançado; `VRAMD_GPU_MODE`/o combo GPU escolhem `auto|local|remote`.

## Arrancar

```bash
# na raiz do AiGameKit
cargo run --manifest-path Studio/Cargo.toml -p studio_app -- --project <dir>
# ou, dentro de Studio/
cargo run -p studio_app -- --project <dir>
```

Sem `--project`, abre na Biblioteca; o workspace por defeito é
`~/AiGameKitStudio` (`AIGAMEKIT_STUDIO_WORKSPACE`). A cache de decode dos GLB
fica em `~/.cache/aigamekit-studio` (`STUDIO_CACHE_DIR`).

Build oficial (debug): `cargo build --manifest-path Studio/Cargo.toml -p studio_app`.

## Projeto em disco

```
<workspace>/<slug>/
  studio.toml          # fonte de verdade (asset, params, run, ui)
  game.yaml            # perfil gerado
  manifest.yaml        # um único asset, gerado
  images/<id>.png      # referência
  meshes/…             # deliverables + _intermediate/ (contrato GameAssets)
  motions/<clip>.npz   # clips motion3d + <clip>.json
  logs/                # resume-*.jsonl, studio-*.log
  .studio/
    trace.jsonl        # AIGAMEKIT_PIPELINE_TRACE_LOG
    preview/           # GLBs de preview de movimento
    history/           # artefactos anteriores ao re-roll
    thumbs/cover.png   # thumbnail da biblioteca
```

`studio.toml` é a fonte de verdade; `game.yaml`/`manifest.yaml` são gerados
atomicamente ao aplicar alterações. Projetos externos sem `studio.toml` não
abrem (adota-os mais tarde).

## Contrato com o GameAssets

O Studio não reimplementa paths nem gates: usa o contrato que já existe.

```bash
gameassets studio check --project <dir> --json
gameassets studio invalidate --project <dir> --from <stage> [--include-stage] --json
gameassets resume --profile <dir>/game.yaml --manifest <dir>/manifest.yaml \
  --no-dashboard --gpu <auto|local|remote> --log <dir>/logs/resume-*.jsonl
```

O progresso vem das linhas JSONL que o `pipeline._stage` emite no stdout
(`id`, `tool`, `status`, `phase`, `percent`), do `--log` (`stages[]`), do
`studio check` e do `vramd queue --json`. As linhas do log de detalhe e o
trace ficam no projeto; a consola da UI mostra as linhas humanas.

`--include-stage` é uma adição retrocompatível ao `studio invalidate`: sem
ela, o artefacto da etapa é preservado (o contrato antigo); com ela, o Studio
apaga também o artefacto e os sidecars. Se o `gameassets` instalado ainda não
tiver a flag, o Studio apaga os paths resolvidos do `check` e repete o
comando sem ela (fallback).

## Viewport

- Os GLB do pool usam `EXT_meshopt_compression`/`KHR_texture_basisu`; o
  `bevy_ext_gltf` descodifica para a cache da app antes de carregar.
- A câmara órbita é adaptada do `editor_camera` do Vael; o enquadramento usa
  o AABB do GLB (`glb_bounds`).
- Materiais são forçados a duplo lado no viewport (armas/folhas de face
  única ficavam invisíveis).
- O clip inicial prefere `idle`, depois `walk`, `run`.
- O wireframe é uma `LineList` construída a partir das malhas estáticas; em
  malhas skinned fica desligado (os vértices estão em bind pose).

## Movimento por texto

`motion3d` é CPU (SprongMotion-14M). O prompt gera um NPZ; o preview usa
`apply-rigged` num GLB em `.studio/preview/`; "Empacotar selecionados" corre
`pack-rigged` para `meshes/_intermediate/<id>_rigged_animated.glb`, invalida
`lod` e corre `resume` para regenerar LODs/colisão. **Substitui o anim-pack
do `animator3d`** quando ambos são usados; misturar os dois exige um merge
(fase seguinte). O rig tem de ter nomes compatíveis com o perfil `sprong22`.

## Thumbnails

A capa é renderizada offscreen (512²) a partir da cena atual, com a câmara
numa vista 3/4, e gravada em `.studio/thumbs/cover.png`; a biblioteca
recarrega-a quando o mtime muda. O botão "Gerar capa" força a atualização.
Hoje só o projeto aberto tem capa; abrir/correr cada projeto preenche a
grelha.

## Flags de desenvolvimento

```bash
# carrega um GLB no viewport e grava um PNG (sem UI)
studio_app --spike-glb <glb> --spike-out /tmp/spike.png [--spike-wireframe]

# UI completa com um projeto e screenshot ao fim de ~3 s
studio_app --project <dir> --ui-screenshot /tmp/ui.png

# abre o projeto e dispara o run quando o primeiro `studio check` chegar
studio_app --project <dir> --auto-run
```

O `--ui-screenshot` é o gate visual barato para verificar o layout sem
interação; o `--spike-*` valida o decode/loader.

## Testes

```bash
cd Studio
cargo test                       # core + app (unitários)
cargo test -p studio_core        # núcleo headless
cargo test -p studio_core --test studio_contract  # gate contra o gameassets real
cargo clippy --all-targets -- -D warnings
cargo fmt --check
```

O `studio_contract` cria um projeto temporário, corre
`gameassets studio check --json` e exige que o payload tenha o asset, o
pipeline e o estado esperados. Salta se o `gameassets` não estiver no PATH.

## VRAM (6 GB)

O `paint3d` pede ~5.2 GB (uint8). Se o worker do `text3d` ainda segurar
~1.4 GB de contexto, o vramd recusa o job. Antes de pintar:

```bash
vramd respawn text3d      # larga o contexto do worker (não mata o daemon)
```

Para caber com margem, baixa a activação no painel Pintura: `max_views`
2–4, `view_resolution` 512 e `render_size` 512. O próprio vramd recusa com
`VRAM insuficiente` em vez de OOM; o erro aparece na consola/tarefas.

## Limitações conhecidas

- Um asset por projeto (o `studio check` exige exatamente uma linha).
- O progresso de shape/paint (waves) é grosso; o `vramd queue` e o watcher de
  artefactos dão o sinal disponível. As barras são indeterminadas onde não há
  percent.
- Cancelar mata o grupo de processos do `resume`, mas não cancela jobs já
  submetidos ao vramd.
- O `studio check` é a fonte de verdade do estado; a UI atualiza-o no início,
  a cada ~2 s durante um run e a cada ~8 s em idle.
- Sem exportação para o pool do Vael (`gameassets handoff`) nesta versão.
- Thumbnails apenas do projeto aberto.
- Hi3D precisa do venv Python 3.10; sem ele o refine é saltado (a UI avisa).
