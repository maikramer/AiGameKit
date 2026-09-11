# Texture2D — Geração de Texturas 2D Seamless

**Idioma:** [English (`README.md`)](README.md) · Português (esta página)

CLI para **texturas 2D seamless (tileable)** usando **Stable Diffusion v1.5**, executando localmente na GPU.

**Seamless 2.0** — tiling em três camadas complementares:

1. **`--seamless-mode late` (default)** — *noise rolling* (latents rodados metade a cada step) + circular padding só nos últimos ~20% dos steps. Receita do [pattern-diffusion](https://huggingface.co/Arrexel/pattern-diffusion) (Apache 2.0): circular constante degrada FID/CLIP de forma mensurável; late+rolling mantém a qualidade **e** a tileabilidade. `full` (circular do início, comportamento clássico) e `off` (SD1.5 puro) continuam disponíveis.
2. **Decode do VAE controlado** — o pipeline devolve sempre latents e a tool decodifica: **decode integral** (sem tiling) sempre que a resolução permite. O `tiled_decode` do diffusers fatura sem wrap e parte a costura circular ao nível do píxel; quando o tiling é inevitável (1024² em VRAM apertada), o latent é primeiro circular-padded e o resultado recortado (preserva o wrap).
3. **Hires via latent upscale + refine** — alvos acima de 512² (nativa do SD1.5) são gerados à nativa, o latent é upscaleado e refinado com uma passagem curta controlada por strength (hires-fix clássico), em vez de gerar directamente acima da nativa (duplica conteúdo). Os tiers `high`/`highest` usam isto automaticamente.

Extras: **VAE ft-mse** por defeito (`TEXTURE2D_VAE_ID=none` desliga), termos de costura no negative base, **score de tileability** no sidecar JSON de cada geração e **auto-heal** opcional da banda de costura quando o score fica abaixo de 0.85.

No monorepo [AiGameKit](../README_PT.md), o pacote depende de [**aigamekit-shared**](../Shared/) (`aigamekit_shared`): presets de qualidade, CLI Rich, helpers de GPU e convenções partilhadas alinhadas com Text2D, Text3D e GameAssets.

## Características

- **Inferência local na GPU** — Stable Diffusion 1.5 + seamless 2.0, sem API cloud; cabe em ~2,5 GB de VRAM (uma GPU de 6 GiB chega)
- **Modos seamless** — `late` (noise rolling + circular tardio; melhor qualidade), `full` (circular clássico), `off`
- **Hires >512²** — geração à nativa + latent upscale + refine (sem sampling directo acima da nativa)
- **Decode que preserva o wrap** — decode integral por defeito; tiling circular-padded só quando a VRAM obriga
- **CFG real** — o negative prompt funciona nativamente (`--negative-prompt`), sem o custo 2x do true-cfg
- **Prompt seamless automático** — acrescenta instruções tileable/seamless automaticamente
- **Score de tileability + auto-heal** — score no sidecar JSON; cross-fade opcional abaixo de 0.85
- **13 presets de materiais** — Wood, Stone, Grass, Sand, Dirt, Metal, Brick, Fabric, Leather, Concrete, Marble, Gravel, Tile Floor
- **Quality tiers** — `fast`, `low`, `medium` (default), `high`, `highest` via `--quality`
- **Batch** — gera múltiplas texturas a partir de um ficheiro de prompts
- **Multi-GPU** — `--gpu-ids 0,1` divide os pesos entre GPUs via accelerate
- **Metadata JSON** — cada textura acompanha um ficheiro `.json` com seed, prompt final e parâmetros
- **Auto-detecção de hardware** — `--hw-auto` deteta o device e a disposição multi-GPU (ligado por defeito)

## Instalação

### Oficial (monorepo)

Na **raiz** do repositório AiGameKit:

```bash
./install.sh texture2d
```

O instalador cria `Texture2D/.venv`, instala o pacote em modo editável e coloca um wrapper em `~/.local/bin`. Guia geral: [docs/INSTALLING_PT.md](../docs/INSTALLING_PT.md) · [EN](../docs/INSTALLING.md).

### Manual / desenvolvimento

```bash
cd Shared && pip install -e .
cd Texture2D && pip install -e .
```

Requer uma **GPU CUDA** (PyTorch, diffusers, transformers e accelerate são dependências de runtime).

## Comandos

| Comando | Descrição |
|---------|-----------|
| `texture2d generate PROMPT` | Gera textura seamless (delega no vramd se disponível) |
| `texture2d presets` | Lista presets de materiais disponíveis |
| `texture2d batch FILE` | Batch a partir de um ficheiro de prompts (um por linha) |
| `texture2d server` | **Deprecated** — usar `vramd start` (vramd) |
| `texture2d server-status` | **Deprecated** — usar `vramd status` |
| `texture2d server-stop` | **Deprecated** — usar `vramd stop` |
| `texture2d info` | Configuração, sistema e ambiente |
| `texture2d skill install` | Instala a Agent Skill do Cursor |
| `texture2d validate-tileable` | Valida a tileability de uma textura |

### `texture2d generate PROMPT`

Gera uma textura seamless (tileable) a partir de um prompt de texto.

```bash
# Uso básico
texture2d generate "rough stone wall surface, medieval castle" -o stone.png

# Com um preset de material
texture2d generate "weathered surface" --preset Stone -o wall.png

# Alta qualidade com seed fixa
texture2d generate "mossy cobblestone" --quality high --seed 42 -o cobble.png

# Negative prompt (CFG nativo do SD1.5, sem custo extra)
texture2d generate "dark marble floor" -n "blurry, watermark" -o marble.png
```

| Parâmetro | Tipo | Default | Descrição |
|-----------|------|---------|-----------|
| `-o, --output` | path | auto (`outputs/textures/`) | Ficheiro de saída (`.png`) |
| `-W, --width` | int | 512 | Largura (múltiplo de 8) |
| `-H, --height` | int | 512 | Altura (múltiplo de 8) |
| `-s, --steps` | int | 30 | Passos de inferência |
| `-g, --guidance` | float | 7.0 | Guidance scale (CFG real) |
| `--seed` | int | None | Seed para reprodutibilidade |
| `-n, --negative-prompt` | str | `""` | Prompt negativo (funciona nativamente com o CFG do SD1.5) |
| `-p, --preset` | str | None | Preset de material (ver Presets abaixo) |
| `-m, --model` | str | None | Override do ID do modelo HF (default: `stable-diffusion-v1-5/stable-diffusion-v1-5`) |
| `--cpu` | flag | `false` | Forçar inferência em CPU |
| `--gpu-ids` | str | None | IDs das GPUs para divisão multi-GPU (ex: `"0,1"`) |
| `--quality` | str | `medium` | Quality tier: `fast`, `low`, `medium`, `high`, `highest` |
| `--hw-auto/--no-hw-auto` | flag | `on` | Auto-detecção de hardware (device + multi-GPU). Sem offload/clamp (SD1.5 cabe em qualquer GPU CUDA) |
| `--ground` | str | `auto` | Modo chão top-down: aplica modificadores de viewpoint/iluminação/escala |
| `--seamless-mode` | str | `late` | `late` (roll + circular tardio, melhor qualidade) · `full` (circular do início) · `off` (SD1.5 puro) |
| `--refine-steps` | int | 12 | Steps do refine hires (alvos >512²); tier `high` usa 12, `highest` 16 |
| `--vae-tiling/--no-vae-tiling` | flag | auto | Decode do VAE: auto = integral sempre que cabe (preserva a costura); `--vae-tiling` força o tiling wrap-preserving |
| `--seam-heal/--no-seam-heal` | flag | on | Cross-fade da banda de borda quando o score de tileability fica < 0.85 |
| `--no-hires` | flag | off | Gerar directamente à resolução pedida (sem 512 + refine) |
| `-v, --verbose` | flag | `false` | Logs detalhados |

> **Nota:** quando `--quality` é definido, resolução e passos são preenchidos a partir do perfil de qualidade **apenas se** o utilizador não passou explicitamente `-W`, `-H`, `-s` ou `-g`. Flags explícitas têm sempre prioridade (resolução soft via `QualityEngine`).

### `texture2d presets`

Lista todos os presets de materiais disponíveis com os respetivos prompts e parâmetros recomendados.

```bash
texture2d presets
```

### `texture2d batch FILE`

Gera texturas em batch a partir de um ficheiro de prompts (um prompt por linha, `#` para comentários).

```bash
texture2d batch prompts.txt -d textures/ --quality high
```

| Parâmetro | Tipo | Default | Descrição |
|-----------|------|---------|-----------|
| `-d, --output-dir` | path | `outputs/textures/` | Diretório de saída |
| `-p, --preset` | str | None | Preset por defeito aplicado a todos os prompts |
| `-W, --width` | int | 512 | Largura |
| `-H, --height` | int | 512 | Altura |
| `-s, --steps` | int | 30 | Passos de inferência |
| `-g, --guidance` | float | 7.0 | Guidance scale |
| `-m, --model` | str | None | Override do ID do modelo HF |
| `--gpu-ids` | str | None | IDs das GPUs para divisão multi-GPU (ex: `"0,1"`) |
| `--quality` | str | `medium` | Quality tier |
| `--hw-auto/--no-hw-auto` | flag | `on` | Auto-detecção de hardware |
| `--ground` | str | `auto` | Modo chão top-down |

### `texture2d info`

Mostra configuração, informações de sistema (Python, PyTorch, CUDA, GPUs), localização da cache HF e caminho de saída por defeito.

```bash
texture2d info
```

### `texture2d skill install`

Instala a Agent Skill do Cursor (`SKILL.md`) no diretório `.cursor/skills/texture2d/` de um projeto de jogo.

| Parâmetro | Tipo | Default | Descrição |
|-----------|------|---------|-----------|
| `-t, --target` | path | `.` | Raiz do projeto do jogo |
| `--force` | flag | `false` | Sobrescrever skill existente |

```bash
texture2d skill install -t /caminho/do/meu-jogo --force
```

### Unified Model Server (vramd)

Preferir **`vramd`**: um socket, evicção VRAM inteligente, fila com
prioridade + afinidade. O `texture2d generate` delega automaticamente (e pode
auto-arrancar o vramd salvo `VRAMD_AUTO_START=0`).

```bash
vramd start
texture2d generate "stone wall" -o stone.png --vramd-stream
texture2d generate "wood" -o wood.png --vramd-priority batch
texture2d generate "test" -o t.png --no-vramd
vramd queue
```

| Flag | Descrição |
|------|-----------|
| `--vramd-priority interactive\|batch` | Prioridade na fila |
| `--no-vramd` | Forçar in-process |
| `--vramd-stream` | Eventos de fila/progresso |

O `texture2d server` per-tool fica **deprecated**. Ver [`Vramd/README.md`](../Vramd/README.md).

## Quality Presets

A flag `--quality` seleciona um perfil de parâmetros pré-configurado. Os perfis apenas preenchem defaults — flags fornecidas explicitamente (`-W`, `-H`, `-s`, `-g`) têm sempre prioridade.

| Perfil | Resolução | Passos | Guidance | Descrição |
|--------|-----------|--------|----------|-----------|
| `fast` | 512×512 | 16 | 7.0 | Preview rápido, qualidade mínima viável |
| `low` | 512×512 | 24 | 7.0 | Qualidade básica, geração mais rápida |
| `medium` | 512×512 | 28 | 7.0 | Qualidade padrão (**default**) |
| `high` | 768×768 | 28 | 7.0 | Hires: 512 nativa + latent upscale + refine 12 |
| `highest` | 1024×1024 | 32 | 7.0 | Hires: 512 nativa + latent upscale + refine 16 |

### Presets de Materiais

Cada preset de material sobrescreve passos e guidance com valores curados:

| Preset | Passos | Guidance | Categoria |
|--------|--------|----------|-----------|
| Wood | 50 | 7.5 | Natural |
| Fabric | 50 | 7.5 | Natural |
| Metal | 60 | 8.0 | Industrial |
| Stone | 50 | 7.5 | Natural |
| Brick | 50 | 7.5 | Arquitetural |
| Leather | 50 | 7.5 | Natural |
| Concrete | 50 | 7.5 | Industrial |
| Marble | 60 | 8.0 | Arquitetural |
| Grass | 30 | 7.0 | Terreno |
| Sand | 30 | 7.0 | Terreno |
| Dirt | 30 | 7.0 | Terreno |
| Gravel | 30 | 7.0 | Terreno |
| Tile Floor | 30 | 7.0 | Arquitetural |

```bash
# Usar um preset com resolução do quality tier
texture2d generate "scratched surface" --preset Metal --quality high -o metal.png
```

## Variáveis de Ambiente

| Variável | Descrição |
|----------|-----------|
| `TEXTURE2D_MODEL_ID` | Override do ID do modelo SD (`stable-diffusion-v1-5/stable-diffusion-v1-5`) |
| `TEXTURE2D_VAE_ID` | Override do VAE (default `stabilityai/sd-vae-ft-mse`); `none` mantém o VAE do checkpoint |
| `TEXTURE2D_HW_AUTO` | Definir como `0` para desativar a auto-detecção de hardware |
| `TEXTURE2D_BIN` | Override do caminho do binário `texture2d` (usado pelo GameAssets) |

## Estrutura de Saída

```
outputs/
└── textures/
    ├── rough_stone_wall_surface_medieval_castle_1715000000.png
    └── rough_stone_wall_surface_medieval_castle_1715000000.json
```

- **PNG** — imagem da textura seamless gerada.
- **JSON** — metadata sidecar com `seed`, `prompt_final`, parâmetros de geração e info do modelo.
- Saída por defeito: `outputs/textures/`. Override com `-o` (generate) ou `-d` (batch).

## Integração no Pipeline

### Materialize (mapas PBR)

Gere uma textura diffuse e usa o [Materialize](../Materialize/) para criar mapas PBR (normal, height, metallic, roughness, ambient occlusion):

```bash
texture2d generate "mossy stone" -o diffuse.png
materialize diffuse.png --output-dir pbr/
```

### Batch no GameAssets

O [GameAssets](../GameAssets/) pode usar `texture2d` como fonte de imagem:

- No `game.yaml`, definir `image_source: texture2d` (global) ou por linha no CSV.
- Com `texture2d.materialize: true` no perfil, o GameAssets gera mapas PBR automaticamente via Materialize.

```bash
gameassets batch --profile game.yaml --manifest manifest.csv
```

Usa `TEXTURE2D_BIN` se o comando `texture2d` não estiver no `PATH`.

## Desenvolvimento

```bash
cd Texture2D

# Instalar em modo editável com dependências de dev
pip install -e ".[dev]"

# Correr testes
pytest tests/ -v

# Lint
ruff check .

# Formatar
ruff format .
```

## Estrutura do Projeto

```
Texture2D/
├── src/texture2d/
│   ├── __init__.py
│   ├── __main__.py            # python -m texture2d
│   ├── _validate_cli.py       # comando validate-tileable
│   ├── cli.py                 # CLI Click (generate, batch, presets, server, info, skill)
│   ├── cli_rich.py            # Integração rich-click
│   ├── client.py              # Cliente do model server
│   ├── cursor_skill/
│   │   └── SKILL.md           # Agent Skill do Cursor
│   ├── generator.py           # Inferência SD1.5 + seamless 2.0 (modos, decode, hires refine, heal)
│   ├── hardware.py            # Perfil de auto-detecção de hardware
│   ├── image_processor.py     # Gravação de imagem + metadata
│   ├── presets.py             # 13 presets de materiais
│   ├── prompt_enhancer.py     # Enhancers de prompt chão/top-down
│   ├── server.py              # Model server (mantém o pipeline carregado)
│   ├── tileability.py         # Helpers de tileability
│   └── utils.py               # Helpers (validação, seeds, formatação)
├── config/
│   └── requirements-dev.txt   # Dependências de desenvolvimento
├── scripts/
│   └── installer.py           # Instalador system-wide
└── tests/
```

## Licença

- **Código:** MIT — [LICENSE](LICENSE).
- **Pesos (default):** [stable-diffusion-v1-5/stable-diffusion-v1-5](https://huggingface.co/stable-diffusion-v1-5/stable-diffusion-v1-5) — licença CreativeML Open RAIL-M; cumpre as restrições de uso do modelo.
- **Tabela completa de licenças:** [AiGameKit/README_PT.md](../README_PT.md) (secção Licenças).
