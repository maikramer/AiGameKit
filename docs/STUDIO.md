# AiGameKit Studio

GUI nativa (Rust/Bevy 0.20 + egui 0.36) para o pipeline de **um asset** do
GameAssets. Código em [`../Studio/`](../Studio/); contrato machine-readable em
`GameAssets/src/gameassets/studio_cmd.py`.

## Porquê

O core do AiGameKit é uma cadeia de CLIs Python (`text2d` → `text3d` →
Hi3D → `paint3d` → `rigging3d` → `animator3d`/`motion3d` → LOD/colisão)
orquestrada por `gameassets resume/batch`. O contrato `gameassets studio`
já foi escrito para um frontend Rust/Bevy; faltava a app. O ArtCraft foi
avaliado e **não** é dependência: é Tauri/React/three.js, com 39 crates
internos `publish = false` e serviços cloud. O Studio copia o modelo de
interação, não o código.

## Arquitetura

```
Studio/                      workspace Rust próprio (precedente: Materialize/)
  crates/studio_core/        sem Bevy; testável e reutilizável
    config.rs                studio.toml (asset, params, pipeline, run, ui)
    project.rs               pasta do projeto, paths, scan do workspace
    manifest.rs              gera game.yaml + manifest.yaml
    check.rs                 payload de studio check (tolerante a extra)
    cli.rs                   CmdSpec de gameassets/motion3d/vramd + env
    runner.rs                spawn, stdout/stderr, JSONL, killpg
    events.rs                ProgressEvent, RunLogRecord, PipelineEvent
    reroll.rs                invalidate, histórico, reset
    motion.rs                clips motion3d + metadados
  crates/studio_app/         Bevy + egui
    app.rs                   workers, ações, polling, run
    state.rs                 StudioState, ações, formulários
    preview/                 cache decode, câmara, cena, animação, thumbnails
    ui/                      topo, esquerda, direita, dock, biblioteca
```

`studio_core` não depende de Bevy. O app usa `vendor/bevy_ext_gltf`
(snapshot do crate do irmão `bevy_extensions`, ver `VENDORED.md`) para
descodificar meshopt/quantização/KTX2 do pool; assim o Studio compila
sozinho, sem o checkout irmão.

## Fluxo

```
UI ──Ação──> app::execute_actions ──CmdSpec──> Runner (thread)
 ▲                                           │ stdout JSONL / stderr / exit
 │                                           ▼
 └─ estado ◄── PipelineEvent ────────────────┘
     ▲
     ├─ CheckWorker: gameassets studio check --json (início, a cada ~2 s no run)
     ├─ VramdWorker: vramd queue --json (1.5 s durante runs)
     └─ Preview: decode do GLB → cache → SceneRoot/WorldAssetRoot → câmara
```

O estado da UI é derivado do `check` (artefactos, `state`, `stale`,
`promotion`, `lock`, `tools`, `gpu`) e dos eventos do run. O `resume` emite
`emit_progress` por etapa do master pipeline; as waves de shape/paint não
têm evento por etapa, por isso a UI usa `vramd queue` + mtimes/estado.

## Etapas e artefactos

| Etapa | Chave (check) | Ficheiro |
|---|---|---|
| Referência | `image` | `images/<id>.png` (gerada ou copiada) |
| Shape | `shape` | `_intermediate/<id>_shape.glb` (+ `.omni.json`) |
| Topologia | `clean` | `_intermediate/<id>_clean.glb` |
| Hi3D | `refined` | `_intermediate/<id>_refined.glb` (+ `.multiview.json`) |
| Orçamento | `to_paint` | `_intermediate/<id>_to_paint.glb` |
| Pintura | `painted` | `_intermediate/<id>_painted.glb` |
| Rig | `rigged` | `_intermediate/<id>_rigged.glb` |
| Animação | `animated` | `_intermediate/<id>_rigged_animated.glb` |
| LODs | `lod0..2` | `meshes/<id>_lod0.glb` … |
| Colisão | `collision` | `meshes/<id>_collision.glb` |
| Precompute | `precompute` | `meshes/<id>_precompute.json` |

`promotion.lod0` diz o que o LOD0 contém (`animated` > `rigged` >
`painted`). `stale.omni`/`stale.refine` vêm dos sidecars e alimentam os
badges amarelos.

## Re-roll

- `invalidate --from X --include-stage` apaga o artefacto de X e os
  derivados, preservando os de cima.
- O Studio copia primeiro os artefactos de X para
  `.studio/history/<stamp>-<stage>/` (compare A/B).
- `image` é reset total.
- Desligar uma etapa no pipeline grava o manifest e invalida a jusante da
  primeira etapa desligada; sem isso um `lod0` antigo continuaria promovido.

## Config e paths

`studio.toml` é a fonte de verdade. `game.yaml`/`manifest.yaml` são gerados
e escritos atomicamente (tmp + rename) ao aplicar. O perfil segue o do Vael
(`assets/game.yaml`): `title/genre/tone/style_preset`, `text3d`, `refine`,
`animator3d`, `lod`, `paint3d`, `collision`. A linha do manifest espelha
`ManifestRow`: `id`, `idea`, `kind`, `category`, `seed`, `pipeline`, `omni`,
`text3d` (só chaves de linha: `octree_resolution`, `mc_level`,
`morph_close`, `split_at_height`, `steps`, …), `refine`, `paint3d`,
`animate`, `collision`, `gpu`.

Atenção: o GameAssets **rejeita chaves desconhecidas** no dict `text3d` da
linha (o `preset` é do perfil, não da linha). O gate
`crates/studio_core/tests/studio_contract.rs` cobre isto contra o
`gameassets` real.

## Movimento por texto

`motion3d` é CPU e não passa pelo vramd:

```bash
motion3d generate "<prompt>" -o motions/<clip>.npz --duration 2 --loop --json
motion3d apply-rigged motions/<clip>.npz <id>_rigged.glb -o .studio/preview/<clip>.glb --clip <clip>
motion3d pack-rigged <id>_rigged.glb -o <id>_rigged_animated.glb \
  --active <clip> -m <clip>=motions/<clip>.npz …
```

Depois: `studio invalidate --from lod` + `resume` (os LODs/colisão são
regenerados do GLB animado). O `pack-rigged` substitui o conjunto de
animações; misturar com o `animator3d game-pack` exige um merge futuro.

## Thumbnails

Câmara offscreen (`RenderTarget::Image` 512², ordem -1) sobre a cena atual,
vista 3/4, `Msaa::Off`; `Screenshot::image` + `ScreenshotCaptured` →
`.studio/thumbs/cover.png`. A grelha da biblioteca esconde-se durante a
captura (gizmos) e a `TextureCache` da UI recarrega pelo mtime.

## Desenvolvimento

```bash
cd Studio
cargo test
cargo test -p studio_core --test studio_contract   # precisa do gameassets
cargo clippy --all-targets -- -D warnings
```

Gates visuais baratos: `--spike-glb <glb> --spike-out <png>` (loader/viewport)
e `--project <dir> --ui-screenshot <png>` (layout).

## VRAM

Num GPU de 6 GB o `paint3d` cabe (~5.2 GB uint8) mas o worker do `text3d`
pode ainda segurar ~1.4 GB de contexto; `vramd respawn text3d` liberta-o
sem parar o daemon. `paint3d.max_views`/`view_resolution`/`render_size`
baixam a activação; o vramd recusa com `VRAM insuficiente` em vez de OOM.

## Roadmap

- Importar um asset de um pack existente (perfil + linha, sem reescrever).
- Exportar para o pool do Vael (`gameassets handoff`).
- Merge de clips `animator3d` + `motion3d`.
- Thumbnails em background para todos os projetos.
- Skeleton/weights no viewport (heatmap do `aigamekit-lab`).
- `studio run` como comando Python único (em vez de o app orquestrar o
  `resume`), se o contrato evoluir.
