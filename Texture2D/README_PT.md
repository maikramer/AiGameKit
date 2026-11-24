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
- **Precisão do pedido** — prompts cabidos na janela de 77 tokens do CLIP sem perder a cauda do utilizador; flags explícitas (e um `--quality` escolhido) ganham ao preset; os portões de qualidade voltam a gerar uma seed que dê um nó, frestas de tábuas ou flocos que o tiling repetiria (`--max-isolated-detail`, `--max-banding`, `--max-tail-ratio`); `--axis` endireita uma textura cujas linhas saíram deitadas; `--match-color` gradua o resultado à cor pedida
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
| `texture2d validate-tileable` | Valida a tileability de uma textura (e, com `--max-isolated-detail`, `--max-banding`, `--max-tail-ratio`, os pormenores que o tiling repetiria, as linhas que a atravessam e os flocos) |

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
| `--max-isolated-detail` | float | off | Limiar (0..1) do pormenor isolado — um nó, uma mancha que o tiling repete em cada célula mesmo com a costura perfeita. Acima dele a seed volta a ser sorteada (ver `--attempts`). Recomendado para madeira: 0.12 |
| `--max-banding` | float | off | Limiar das bandas por eixo — frestas de tábuas ou linhas que atravessam a textura inteira (medido: 0,87 em tábuas, 0,68 em veio deitado, 0,20–0,44 em veio direito). Acima dele a seed volta a ser sorteada. Recomendado para madeira: 0.5 |
| `--max-tail-ratio` | float | off | Limiar da cauda pesada — flocos ou pontos claros sobre um fundo liso (medido: 28 numa cal com flocos, 1,5–3,2 no resto). Acima dele a seed volta a ser sorteada. Recomendado para madeira: 5 |
| `--axis` | `v`/`u` | off | A textura tem uma direção: `v` = as linhas correm de cima a baixo (veio de madeira), `u` = da esquerda à direita. Roda 90° quando saírem ao contrário — o SD1.5 não obedece a «vertical grain» |
| `--match-color` | `#RRGGBB` | off | A cor pedida, como cor média. O SD1.5 tem um prior de tom médio (todos os prompts de madeira saíram castanhos, a 96–128 de 255): a imagem é graduada até ao alvo e mantém a estrutura (`match_color` no sidecar) |
| `--attempts` | int | 1 | Gerações no máximo quando há um portão de qualidade; se nenhuma passar, devolve a que passa mais perto (o sidecar assinala `quality_gates`) |
| `--no-hires` | flag | off | Gerar directamente à resolução pedida (sem 512 + refine) |
| `-v, --verbose` | flag | `false` | Logs detalhados |

> **Nota:** quando `--quality` é definido, resolução e passos são preenchidos a partir do perfil de qualidade **apenas se** o utilizador não passou explicitamente `-W`, `-H`, `-s` ou `-g`. Flags explícitas têm sempre prioridade (resolução soft via `QualityEngine`). O mesmo vale contra o
preset de material: o preset só preenche o que NÃO foi declarado (um `-s`/`-g`/`-W`/`-H` explícito,
ou um `--quality` escolhido); sem nenhum deles o preset ganha, como antes.

### Precisão do pedido

O codificador de texto do SD1.5 lê 77 tokens por prompt — no positivo e no negativo. O que passa
disso cai sem erro nem aviso. Seis garantias:

- **A cauda do utilizador nunca é o que cai.** Quando `preset + prompt` cabe, o texto é o clássico
  (preset à cabeça). Quando não cabe, o pedido do utilizador vem inteiro e o preset só empresta as
  cláusulas que couberem — `preset_dropped` no sidecar lista as restantes. Um prompt demasiado
  longo por si é cortado numa fronteira de vírgula e assinalado em `prompt_user_truncated`.
- **Negative prompt sem desperdício.** Só quando o negativo não cabe: as repetições saem e as
  cláusulas ficam por prioridade (a base da ferramenta, depois o utilizador, depois o preset).
  `negative_dropped` diz o que ficou de fora.
- **Nenhuma esquisitice repetida.** `isolated_detail.spot` no sidecar mede o quanto o pormenor
  pequeno mais destacado foge ao resto da textura (0..1, mais baixo = melhor: as texturas do pool
  ficam abaixo de ~0,10; um nó com halo dá 0,17–0,30). Com `--max-isolated-detail 0.12` uma seed
  reprovada volta a gerar com `seed + 1`. Uma mancha que reprova em todas as
  seeds é o **decode do VAE em tiles**, não a seed: deixa uma borra por tile
  (medido 0,40 contra 0,08 no decode integral). A política automática esvazia
  o cache do PyTorch antes de medir a VRAM livre, pelo que uma GPU de 6 GB
  fica com o decode integral; `--no-vae-tiling` força-o.
- **Sem tábuas, sem veio deitado.** `banding` no sidecar é, por eixo, o quanto
  uma linha inteira da textura se destaca (em unidades do percentil 99 do
  passa-bando): frestas de tábuas dão 0,87, veio deitado 0,68, veio direito
  0,20–0,44. `--max-banding 0.5` re-genera uma seed com frestas; `--axis v` roda
  uma textura cujas linhas saíram da esquerda para a direita (só quando um eixo
  domina claramente — uma corda diagonal fica como está; `axis.rotated` diz se
  rodou).
- **Sem flocos.** Muitos pontos claros pequenos sobre um fundo liso cabem
  dentro do percentil 99, e o portão do pormenor isolado não os vê.
  `tail_ratio` é a distância do percentil 99,9 à mediana em unidades do
  percentil 95 (uma textura densa dá 1,5–3,2; uma cal com flocos deu 28).
  `--max-tail-ratio 5`.
- **A cor pedida.** O SD1.5 tem um prior de tom médio: nove prompts de madeira
  — de um pinho quase preto a uma cal marfim — saíram todos castanhos, com
  luminância média de 96–128 em 255. `--match-color #RRGGBB` gradua a imagem
  até a sua cor média ser o alvo, com uma única escala de contraste partilhada
  pelos canais que mantém a estrutura (o veio quente continua quente sobre um
  alvo escuro) e só comprime o contraste quando o alvo está perto do preto ou
  do branco (`match_color` no sidecar: `before`, `after`, `scale`).

Com vários portões, cada tentativa reprovada volta a gerar; se nenhuma passar,
devolve a de menor razão valor/limiar e `quality_gates` diz que portão falhou
(`best_of`).

Os dois prompts trazem `prompt_tokens` / `negative_prompt_tokens` no sidecar.

`texture2d validate-tileable IMAGE --max-isolated-detail 0.12 --max-banding 0.5
--max-tail-ratio 5` aplica os mesmos portões a um ficheiro já gerado (exit 1
acima de qualquer limiar).

```bash
# Um pinho escuro manchado: veio ao longo de V, a cor da paleta, sem frestas nem flocos
texture2d generate "very dark stained pine wood, fine straight vertical grain" \
  --quality high -W 1024 -H 1024 --seed 51121 \
  --max-isolated-detail 0.12 --max-banding 0.5 --max-tail-ratio 5 --attempts 6 \
  --axis v --match-color "#2F2D25" -o pine.png
```

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
│   ├── prompt_budget.py       # Janela de 77 tokens do CLIP: cabimento dos prompts
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
