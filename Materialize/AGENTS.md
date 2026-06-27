# Materialize CLI — Para agentes de IA

**O que é:** CLI em Rust que gera mapas PBR (Height, Normal, Metallic, Smoothness/Roughness, Edge, AO, Curvature opcional) a partir de uma imagem de textura difusa, usando compute shaders na GPU (wgpu).

**Quando usar:** Sempre que for preciso gerar mapas PBR a partir de uma textura (ex.: para jogos, rendering 3D, materiais).

**Quando não usar:** Redimensionar imagem, converter formato sem gerar PBR, edição de imagem genérica — use outras ferramentas.

## Sintaxe

```bash
materialize <INPUT> [-o DIR] [-f FORMAT] [-p PRESET] [-q 0-100] [-v] [OPTIONS]
```

`INPUT` pode ser arquivo, diretório ou glob (`./textures/*.png`).

| Argumento/flag | Obrigatório | Padrão | Descrição |
|----------------|-------------|--------|-----------|
| `INPUT`        | Sim*        | —      | Caminho da imagem/dir/glob de entrada (png, jpg, tga, exr). *Opcional só para `--list-*`, `--generate-completions`, subcomandos. |
| `-o`, `--output` | Não      | `.`    | Diretório de saída |
| `-f`, `--format` | Não      | `png`  | Formato dos arquivos: `png`, `jpg`, `tga`, `exr` |
| `-p`, `--preset` | Não      | `default` | Preset de material (ver abaixo) |
| `-q`, `--quality` | Não     | `95`   | Qualidade JPEG (1–100), quando `-f jpg` |
| `-v`, `--verbose` | Não     | —      | Saída verbosa (timing por estágio + auto-detect) |
| `--quiet`      | Não         | —      | Não listar arquivos gerados no sucesso |
| `--include-curvature` | Não | —    | Gerar `texture_curvature.png` (7º mapa, opt-in) |
| `--roughness`  | Não         | —      | Exportar `texture_roughness.png` (= `1 - smoothness`) em vez de `_smoothness.png` |
| `--normal-format` | Não     | `opengl` | `opengl` (Y-up) ou `directx` (Y-down) |
| `--only MAPS`  | Não         | —      | Whitelist: `height,normal,metallic,smoothness,edge,ao,curvature` |
| `--skip MAPS`  | Não         | —      | Blacklist (mutuamente exclusivo com `--only`) |
| `--seamless` / `--no-seamless` | Não | auto | Forçar wrap/clamp nas bordas |
| `--jobs N`     | Não         | `1`    | Paralelismo CPU (batch); GPU fica serial |
| `--skip-existing` | Não     | —      | Pular imagens já processadas (resume) |
| `--progress`   | Não         | —      | Mostrar `[i/N]` por imagem no batch |
| `--list-presets` | Não       | —      | Listar presets e sair |
| `--list-maps`  | Não         | —      | Listar mapas gerados e sair |
| `--generate-completions SHELL` | Não | — | Bash/Zsh/Fish/Elvish/PowerShell |

### Overrides inline (em cima do preset)

`--height-contrast`, `--height-blur`, `--normal-strength`, `--metallic-scale`,
`--metallic-local-variance`, `--smoothness-base`, `--smoothness-boost`,
`--smoothness-roughness`, `--edge-contrast`, `--ao-depth-scale`.

### Subcomandos

- `materialize info <image>` — Analisa textura (auto-detect + features) sem gerar.
- `materialize skill install` — Instala skill Cursor no cwd.

## Presets

19 presets + `auto`:

`default`, `skin`, `floor`, `metal`, `fabric`, `wood`, `stone`, `concrete`,
`leather`, `marble`, `sand`, `foliage`, `plaster`, `asphalt`, `brick`, `ice`,
`snow`, `lava`, `water`, `auto`.

`-p auto` faz análise CPU (luminance, saturação, hue histogram, edge density,
local contrast variance, tile MSE, alpha) e escolhe o melhor preset + aplica
auto-tile (wrap sampling quando `tile_mse < 0.005`) e auto-scale (ajusta
`height_contrast`/`normal_strength` por `edge_density`).

## Exemplos

```bash
# Básico
materialize texture.png

# Textura de pele
materialize skin_diffuse.png -p skin -o ./out/

# Auto-detect
materialize texture.png -p auto -v

# Análise sem gerar
materialize info texture.png

# Batch com resume
materialize ./textures/ -o ./pbr/ --jobs 4 --skip-existing --progress

# Só height + normal
materialize texture.png --only height,normal -o ./out/

# Curvature + roughness
materialize texture.png --include-curvature --roughness -o ./out/

# DirectX normals
materialize texture.png --normal-format directx -o ./out/
```

**Arquivos gerados** (a partir de `texture.png`):
- `texture_height.png`
- `texture_normal.png`
- `texture_metallic.png`
- `texture_smoothness.png` (ou `_roughness.png` com `--roughness`)
- `texture_edge.png`
- `texture_ao.png`
- `texture_curvature.png` (só com `--include-curvature`)

## Variáveis de ambiente

| Variável | Descrição |
|----------|-----------|
| `MATERIALIZE_GPU_BACKEND` | `vulkan\|metal\|dx12\|gl\|primary` (default: `primary`) |
| `MATERIALIZE_LOG` | `error\|warn\|info\|debug\|trace` (default: `warn`) |

## Códigos de saída

| Código | Significado |
|--------|-------------|
| `0`    | Sucesso |
| `1`    | Erro genérico |
| `2`    | Input não encontrado |
| `3`    | Formato não suportado |
| `4`    | Erro de GPU |
| `5`    | Erro de I/O |
| `6`    | Imagem grande demais para GPU |

Sempre verificar o exit code após invocar; em falha, usar stderr para diagnóstico.

## Documentação completa

- [docs/cli-api.md](docs/cli-api.md) — Referência da CLI
- [docs/README.md](docs/README.md) — Visão geral e instalação
- [CHANGELOG.md](CHANGELOG.md) — Release history (2.0 breaking changes)
