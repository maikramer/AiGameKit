# Texture2D

**Documentação:** [English (`README.md`)](README.md) · Português (esta página)

CLI para geração de texturas 2D seamless (tileable) localmente em GPU com [**pattern-diffusion**](https://huggingface.co/Arrexel/pattern-diffusion) e **mapas PBR** via [Materialize](../Materialize/).

Usa o modelo [Arrexel/pattern-diffusion](https://huggingface.co/Arrexel/pattern-diffusion) — fine-tune de StableDiffusion-2-base treinado em **6,8M de padrões tileable** (Apache-2.0) — para gerar texturas que repetem sem costuras visíveis, ideal para chão, rochas, paredes e materiais de game dev. **PBR** opcional (normal / height / metallic / roughness / AO) é produzido pelo CLI Rust/wgpu [Materialize](../Materialize/).

No monorepo [GameDev](../README_PT.md), o pacote depende de [**gamedev-shared**](../Shared/) (`gamedev_shared`): presets de qualidade, CLI Rich, helpers de GPU e utilitários alinhados com Text2D/Text3D/GameAssets.

## Características

- **Inferência local em GPU** — pattern-diffusion (fine-tune SD2-base, Apache-2.0), sem cloud
- **Seamless por construção** — o modelo é treinado em padrões tileable e a inferência usa padding circular em `Conv2d` (`make_seamless`); a recipe opcional `--seamless-method full` (noise-rolling) tem **zero perda mensurável de FID/CLIP** na costura (cf. [model card](https://huggingface.co/Arrexel/pattern-diffusion))
- **PBR via Materialize** — quando o binário `materialize` está no `PATH` (ou `MATERIALIZE_BIN` está definido), `texture2d generate` deriva automaticamente normal / height / metallic / roughness / AO
- **13 presets de materiais** — Wood, Stone, Grass, Sand, Dirt, Metal, Brick, etc.
- **Tiers de qualidade** — `fast`, `low`, `medium` (default), `high`, `highest` via `--quality`
- **Quantização** — `--quant {none,fp8,nf4}` para reduzir VRAM (default `none`)
- **Batch** — gera múltiplas texturas a partir de um ficheiro de prompts
- **Multi-GPU** — `--gpu-ids 0,1` divide pesos entre GPUs via accelerate
- **Metadata JSON** — cada textura acompanha ficheiro `.json` com seed, prompt final, parâmetros
- **Modo low VRAM** — CPU offload para GPUs mais pequenas

## Arranque rápido

```bash
# 1. Setup (venv + deps)
./scripts/setup.sh

# 2. Ativar
source .venv/bin/activate

# 3. Gerar
texture2d generate "rough stone wall surface, medieval castle" -o stone.png

# 4. Usar preset
texture2d generate "weathered surface" --preset Stone -o wall.png
```

## Instalação

### Oficial (monorepo)

Na **raiz** do repositório GameDev:

```bash
cd /caminho/para/GameDev
./install.sh texture2d
# Windows: .\install.ps1 texture2d
```

O instalador **cria** `Texture2D/.venv` se não existir, instala em modo editável e gera wrappers em `~/.local/bin`. Lista de ferramentas: `./install.sh --list`. Guia geral: [docs/INSTALLING_PT.md](../docs/INSTALLING_PT.md) · [EN](../docs/INSTALLING.md)

### Manual / desenvolvimento

```bash
./scripts/setup.sh
source .venv/bin/activate
```

O `setup.sh` instala `gamedev-shared` a partir de `../Shared` e o pacote `texture2d` em modo editável (conveniência dev; não substitui o fluxo oficial acima).

### Atalho local

```bash
python3 scripts/installer.py --prefix ~/.local
# ou: ./scripts/run_installer.sh / ./scripts/install.sh
python3 scripts/installer.py --use-venv
```

Sem GPU cloud — a inferência é local. Requer **CUDA GPU** (PyTorch, diffusers, transformers, accelerate são dependências de runtime).

## Comandos

| Comando | Descrição |
|---------|-----------|
| `texture2d generate PROMPT` | Gera uma textura seamless |
| `texture2d presets` | Lista presets de materiais |
| `texture2d batch FILE` | Batch a partir de ficheiro (um prompt por linha) |
| `texture2d info` | Configuração e ambiente |
| `texture2d skill install` | Instala Agent Skill Cursor |

## Parâmetros de `generate`

| Parâmetro | Default | Descrição |
|-----------|---------|-----------|
| `--output/-o` | auto | Ficheiro de saída (.png) |
| `--width/-W` | 1024 | Largura (256–2048, múltiplo de 8) |
| `--height/-H` | 1024 | Altura |
| `--steps/-s` | 50 | Passos de inferência (10–100) |
| `--guidance/-g` | 7.5 | Guidance scale (1.0–20.0) |
| `--seed` | aleatório | Seed para reprodutibilidade |
| `--negative-prompt/-n` | "" | Prompt negativo |
| `--preset/-p` | None | Preset de material |
| `--seamless-method` | `late` | Estratégia seamless: `none`, `late` (padding circular — default), `full` (noise-rolling, garantia máxima) |
| `--quant` | `none` | Quantização do modelo: `none`, `fp8`, `nf4` (menos VRAM) |
| `--model/-m` | `Arrexel/pattern-diffusion` | Modelo HF |
| `--no-pbr` | `false` | Saltar o passo PBR automático do Materialize |
| `--quality` | `medium` | Tier de qualidade: `fast`, `low`, `medium`, `high`, `highest` |
| `--gpu-ids` | None | GPUs para split multi-GPU (ex. `"0,1"`) |
| `--low-vram` | `false` | CPU offload (menos VRAM) |

## Configuração

| Variável | Descrição |
|----------|-----------|
| `HF_TOKEN` | Token Hugging Face (usado para descarregar os pesos pattern-diffusion do Hub) |
| `TEXTURE2D_MODEL_ID` | Override do modelo (default: `Arrexel/pattern-diffusion`) |
| `TEXTURE2D_BIN` | Override do binário `texture2d` (usado pelo GameAssets) |
| `MATERIALIZE_BIN` | Override do binário `materialize` (passo PBR automático) |

> **Nota:** a inferência corre **localmente em GPU** (CUDA). O `HF_TOKEN` só é necessário para descarregar os pesos do Hub da Hugging Face na primeira execução; as execuções subsequentes usam a cache local (`~/.cache/huggingface`, override com `HF_HOME`).

## Integração com Materialize

Por defeito, `texture2d generate` corre o passo PBR **automaticamente** quando o binário `materialize` está no `PATH` (ou `MATERIALIZE_BIN` definido): depois de produzir a textura seamless diffuse, invoca o [Materialize](../Materialize/) para derivar normal / height / metallic / roughness / ambient-occlusion na mesma pasta de saída.

```bash
# Um comando — diffuse + PBR (quando Materialize está disponível)
texture2d generate "mossy stone" -o mossy_stone.png
# escreve mossy_stone.png + mossy_stone_normal.png + mossy_stone_height.png + ...

# Fluxo explícito em dois passos (ou quando Materialize não está instalado)
texture2d generate "mossy stone" --no-pbr -o diffuse.png
materialize diffuse.png --output-dir pbr/
```

Se o Materialize **não** for detetado, o `texture2d` emite um aviso de uma linha e salta o passo PBR (a diffuse seamless continua a ser gerada). Instala com `./install.sh materialize` na raiz do monorepo, ou define `MATERIALIZE_BIN` com o caminho do binário.

## Integração com GameAssets

O [GameAssets](../GameAssets/) pode usar `texture2d` como fonte de imagem:

- No `game.yaml`, definir `image_source: texture2d` (global) ou por linha no CSV com coluna `image_source`.
- Com `texture2d.materialize: true` no perfil, o GameAssets gera mapas PBR automaticamente via Materialize.

```bash
gameassets batch --profile game.yaml --manifest manifest.csv
```

Variável `TEXTURE2D_BIN` se o comando não estiver no `PATH`.

## Estrutura

```
Texture2D/
├── src/texture2d/
│   ├── cli.py             # CLI Click (generate, batch, presets, info)
│   ├── generator.py       # Inferência pattern-diffusion + PBR Materialize
│   ├── presets.py         # 13 presets de materiais
│   ├── image_processor.py # Processamento de imagem
│   └── utils.py           # Utilitários
├── config/
│   ├── requirements.txt
│   └── requirements-dev.txt
├── scripts/
│   ├── setup.sh           # Setup do venv + deps
│   ├── run_installer.sh   # Chama installer.py
│   ├── install.sh         # Delega para run_installer.sh
│   └── installer.py       # Lógica partilhada com gamedev-install
└── tests/
```

## Testes

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

## Licença

- **Código:** MIT — [LICENSE](LICENSE).
- **Pesos (default):** [Arrexel/pattern-diffusion](https://huggingface.co/Arrexel/pattern-diffusion) — **Apache-2.0** (fine-tune de StableDiffusion-2-base em 6,8M de padrões tileable). Lê o model card antes de distribuir ou usar em produção.
- **Tabela completa:** [GameDev/README_PT.md](../README_PT.md) (secção Licenças).
