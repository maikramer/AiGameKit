---
name: materialize-cli
description: Gera mapas PBR (height, normal, metallic, smoothness, edge, AO) a partir de texturas difusas via compute shaders wgpu. Use com Materialize CLI, baking PBR, pipeline diffuse→material, WGSL, texture2d.materialize no GameAssets, ou quando o utilizador mencionar materialize-cli ou mapas PBR a partir de imagem.
---

# Materialize CLI

## Quando usar

- Explicar o **CLI** (argumentos, ficheiros de saída, formatos).
- Alterar ou adicionar um **mapa PBR** (shader WGSL + pipeline Rust + I/O).
- Depurar ou estender o pipeline **GPU** (wgpu, bind groups, formatos).
- Escrever ou atualizar documentação (`README`, `docs/`).

## Visão geral para utilizadores

**Comando:** `materialize <entrada> [opções] [subcomando]` (`INPUT` = ficheiro, diretório ou glob)

**Saídas (6 + 1 opcional):** `{stem}_height.*`, `_normal.*`, `_metallic.*`, `_smoothness.*` (ou `_roughness.*` com `--roughness`), `_edge.*`, `_ao.*`, e `_curvature.*` (só com `--include-curvature`).

**Opções principais:** `-o` / `--output`, `-f` / `--format` (png \| jpg \| tga \| exr), `-p` / `--preset`, `-q` / `--quality` (1–100, JPEG), `-v` / `--verbose`, `--quiet`, `--include-curvature`, `--roughness`, `--normal-format` (opengl \| directx), `--only`/`--skip` (whitelist/blacklist de mapas), `--seamless`/`--no-seamless`, `--jobs`, `--skip-existing`, `--progress`, `--list-presets`, `--list-maps`, `--generate-completions`. Overrides inline: `--height-contrast`, `--height-blur`, `--normal-strength`, `--metallic-scale`, `--metallic-local-variance`, `--smoothness-base`, `--smoothness-boost`, `--smoothness-roughness`, `--edge-contrast`, `--ao-depth-scale`.

**Subcomandos:** `materialize info <imagem>` (analisa sem gerar), `materialize skill install`.

**Presets (19 + auto):** `default`, `skin`, `floor`, `metal`, `fabric`, `wood`, `stone`, `concrete`, `leather`, `marble`, `sand`, `foliage`, `plaster`, `asphalt`, `brick`, `ice`, `snow`, `lava`, `water`, `auto`. `-p auto` faz análise CPU (luminância, saturação, hue, edges, contraste local, tile MSE, alpha) e escolhe o melhor preset + auto-tile (`tile_mse < 0.005`).

**Variáveis de ambiente:** `MATERIALIZE_GPU_BACKEND` (`vulkan|metal|dx12|gl|primary`), `MATERIALIZE_LOG` (`error|warn|info|debug|trace`).

**Exit codes:** `0` sucesso · `1` genérico · `2` input não encontrado · `3` formato não suportado · `4` GPU · `5` I/O · `6` imagem grande demais.

```bash
materialize textura.png -o ./out/ -v
materialize skin.png -p skin -o ./out/
materialize textura.png -p auto -v
materialize ./textures/ -o ./pbr/ --jobs 4 --progress --skip-existing
materialize textura.png --include-curvature --roughness --only height,normal,curvature -o ./out/
materialize info textura.png        # análise sem gerar
materialize skill install           # instala esta skill em .cursor/skills/ do projeto atual
```

## Integração com GameAssets / Texture2D

- **GLB 3D:** **Hunyuan3D-Paint 2.1** (`paint3d texture`) já exporta GLB PBR; não uses Materialize no mesh.
- **Imagem difusa:** com **`texture2d.materialize: true`** no `game.yaml`, o GameAssets chama `materialize <png> -o …` após `texture2d generate`.
- Variável de ambiente: **`MATERIALIZE_BIN`** se o executável não estiver no `PATH`.

## Layout do código (referência para contribuidores)

| Área | Caminho | Papel |
|------|---------|--------|
| Shaders | `src/shaders/*.wgsl` | Um compute shader por mapa (incl. `curvature.wgsl`, opt-in); workgroup 8×8; `struct Params` uniform 64 bytes |
| Pipeline | `src/pipeline.rs` | Ordem: height → normal → metallic → smoothness → edge → AO → curvature |
| Análise | `src/analyze.rs` | Auto-deteção CPU (features) para `-p auto` / `info` |
| GPU | `src/gpu.rs` | Pipelines 1 ou 2 inputs, bind groups; helpers `sample_coord` (clamp/wrap) |
| I/O | `src/io.rs` | `get_output_paths`, conversões, `save_image` |
| Presets | `src/preset.rs` | Enum `Preset` (19 + auto), struct `PresetParams` (GPU uniform 64 bytes), valores por tipo |
| CLI | `src/cli.rs` | Args clap; enums `OutputFormat`, `Preset`, subcomandos `info`/`skill` |
| Main | `src/main.rs` | Imagem → `pipeline.process()` → gravar os mapas (6 + curvature opcional) |

## Dependências entre mapas

- **Height:** a partir do diffuse.
- **Normal:** a partir do height (flip-Y via uniform `normal_flip_y`, `--normal-format`).
- **Metallic:** a partir do diffuse (detetor de dois níveis + local-variance damping).
- **Smoothness:** a partir do diffuse + metallic (pipeline a 2 entradas; contraste local 5×5).
- **Edge:** a partir do normal (magnitude do gradiente).
- **AO:** a partir do height (cavity-style).
- **Curvature (opt-in):** a partir do height (Laplaciano), só com `--include-curvature`.

Todos os shaders de vizinhança usam `sample_coord` (clamp/wrap controlado por `params.seamless`).

## Adicionar ou alterar um mapa

1. **Shader:** `src/shaders/<nome>.wgsl` com `@group(0) @binding(0)` entrada e `@binding(1)` storage output (ou 0,1,2 para 2 entradas). `@group(1) @binding(0)` uniform `Params` (preset). Workgroup 8×8. Guardar com `coords >= dims` → return.
2. **Pipeline:** em `pipeline.rs`, `include_str!`, criar pipeline, textura de saída, bind group, dispatch após dependências, readback para `PbrMaps`.
3. **I/O:** em `io.rs`, estender `OutputPaths`, `get_output_paths`, `*_to_image`, gravar em `main.rs`.
4. **Formatos:** height em R32Float onde aplicável; restantes conforme comentários em `io.rs` / `main.rs`.

## Testes

```bash
cargo build
cargo test
materialize /caminho/diffuse.png -o ./out/ -v
```

Testes de integração: `tests/integration_test.rs`. Unitários em `io::tests`.

## Documentação

- **Utilizador:** `README.md`, `docs/README.md`, `docs/features.md`, `docs/cli-api.md`
- **Técnico:** `docs/architecture.md`, `docs/algorithms.md`, `docs/shaders.md`
- **Planeamento:** `docs/roadmap.md`, `docs/plans/*.md`

Manter docs alinhados ao adicionar mapas ou opções CLI.

## Ferramentas relacionadas

| Ferramenta | Ligação |
|------------|---------|
| **Text3D / Paint3D** | PBR no GLB vem do Paint 2.1; ver `Text3D/docs/PBR_MATERIALIZE.md`. |
| **GameAssets** | Materialize só com **`texture2d.materialize`** (mapas a partir da difusa). |
