# GameDev

**Documentação:** [English (`README.md`)](README.md) · Português (esta página)

[![CI](https://github.com/maikramer/GameDev/actions/workflows/ci.yml/badge.svg)](https://github.com/maikramer/GameDev/actions)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Rust](https://img.shields.io/badge/rust-1.75+-orange.svg)](https://www.rust-lang.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](Text2D/LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

Monorepo com ferramentas de **texto→imagem**, **texto→3D**, **texto→áudio**, **texturas e skymaps** (API Hugging Face), **texturização PBR**, **decomposição em partes**, **rigging**, **animação** e **batch de assets**, partilhando a mesma base (`gamedev-shared`), instalador unificado e documentação.

## Projetos

| Pasta | Descrição |
|-------|-----------|
| [**Shared**](Shared/) | Biblioteca partilhada (`gamedev-shared`): logging, GPU, subprocess, instaladores, CLI. |
| [**Text2D**](Text2D/) | CLI **text-to-image** com FLUX (quantização SDNQ), orientada a GPU modesta. |
| [**Text3D**](Text3D/) | Pipeline **text-to-3D**: imagem 2D (via Text2D) → mesh GLB com Hunyuan3D-2.1 (SDNQ INT4). Textura via Paint3D (opcional). |
| [**Part3D**](Part3D/) | **Partes semânticas 3D**: Hunyuan3D-Part (segmentação / partes em mesh). |
| [**Paint3D**](Paint3D/) | **Texturização 3D**: Hunyuan3D-Paint 2.1 (PBR multivista) + Materialize PBR + Upscale IA (Real-ESRGAN). Standalone ou via Text3D. |
| [**GameAssets**](GameAssets/) | **Batch de prompts/assets**: perfil + CSV → `text2d` ou `texture2d` + opcional `text3d`, rig, **Animator3D** (auto-detetado), **`gameassets dream`** (ideia → scaffold Vite). |
| [**Texture2D**](Texture2D/) | **Texturas 2D seamless** (tileable) via HF Inference API — sem GPU local. |
| [**Skymap2D**](Skymap2D/) | **Skymaps equirectangular 360°** via HF Inference API — skyboxes para game dev, sem GPU local. |
| [**Text2Sound**](Text2Sound/) | CLI **text-to-audio** com Stable Audio Open 1.0: áudio estéreo 44.1 kHz, presets para game dev. |
| [**Rigging3D**](Rigging3D/) | **rigging3d** — auto-rigging 3D com [**UniRig**](https://github.com/VAST-AI-Research/UniRig) (skeleton + skinning + merge); GPU CUDA; Python **3.11**, **bpy** 5.0.x (Open3D). |
| [**Animator3D**](Animator3D/) | **animator3d** — **bpy** 5.1; Python **3.13**; clips procedimentais, **`game-pack`** (presets humanoid/creature/flying), export GLB após rigging. |
| [**Materialize**](Materialize/) | CLI **PBR maps** (Rust/wgpu): gera normal, AO, metallic, smoothness a partir de textura difusa. |
| [**GameDevLab**](GameDevLab/) | **Lab CLI**: debug 3D, bancos de quantização, profiling, otimização de pipeline. |
| [**Terrain3D**](Terrain3D/) | **terrain3d** — Geração de terreno por IA via modelos de difusão (terrain-diffusion; CUDA GPU). |
| [**VibeGame**](VibeGame/) | **vibegame** — motor 3D em TypeScript (ECS, Three.js, XML declarativo); **Bun** + **Vite**. Ver [VibeGame/README.md](VibeGame/README.md). |

Cada projeto tem o seu próprio `README`, `setup`, requisitos e licença.

## Arquitectura

```
GameDev/
  Shared/           ← gamedev-shared (pip): logging, GPU, subprocess, env, instaladores
  Text2D/           ← text2d (pip) — depende de Shared
  Text3D/           ← text3d (pip) — depende de Shared + Text2D; textura via Paint3D (opcional)
  Part3D/           ← part3d (pip) — Shared; Hunyuan3D-Part (torch-scatter/cluster)
  Paint3D/           ← paint3d (pip) — depende de Shared; Hunyuan3D-2.1 hy3dpaint + Materialize PBR + Upscale
  GameAssets/        ← gameassets (pip) — depende de Shared; chama text2d/texture2d/text3d via subprocess
  Texture2D/         ← texture2d (pip) — depende de Shared; inferência HF na cloud
  Skymap2D/          ← skymap2d (pip) — depende de Shared; skymaps equirectangular via HF
  Text2Sound/        ← text2sound (pip) — depende de Shared; Stable Audio Open 1.0
  Rigging3D/         ← rigging3d (pip) — Shared; inferência Py 3.11 + bpy 5.0.x
  Animator3D/        ← animator3d (pip) — Shared; Py 3.13 + bpy 5.1 (animação)
  GameDevLab/        ← gamedev-lab (pip) — depende de Shared; debug 3D, benches, profiling
  Terrain3D/        ← terrain3d (pip) — depende de Shared; geração de terreno por IA via difusão
  Materialize/       ← materialize-cli (cargo) — instalador Python usa Shared
  VibeGame/          ← vibegame (npm/Bun + Vite) — motor 3D no browser; standalone, não é pip
```

## Requisitos gerais

- **Python**: a maioria das ferramentas pede **3.10+**; exceções: **Rigging3D** (3.11), **Animator3D** (3.13 + `bpy` 5.1). Ver README de cada pasta.
- **VibeGame** usa **Bun** e ferramentas compatíveis com **Node** (ver `VibeGame/package.json`); na raiz do repositório, `make test-vibegame` após instalar o Bun.
- **GPU** opcional no Text2D; no Text3D/Paint3D/Part3D/Rigging3D, CUDA com VRAM suficiente é recomendado para tempos aceitáveis. **Texture2D** e **Skymap2D** não precisam de GPU local (API Hugging Face). **GameAssets** só exige GPU se o perfil/linha invocar ferramentas locais (ex. text2d, text3d). **Multi-GPU:** a maioria das ferramentas com GPU aceita `--gpu-ids 0,1` para dividir pesos do modelo entre várias GPUs NVIDIA via accelerate.
- Os **pesos dos modelos** (Hugging Face, etc.) têm licenças próprias — consulta os model cards antes de distribuir ou usar em produção.

## Arranque rápido

Guia completo em português: **[docs/INSTALLING_PT.md](docs/INSTALLING_PT.md)**. Versão em inglês: [docs/INSTALLING.md](docs/INSTALLING.md).

**Pipeline jogo (GameAssets → Vite / VibeGame, pastas, handoff GLB):** [docs/MONOREPO_GAME_PIPELINE.md](docs/MONOREPO_GAME_PIPELINE.md) (documento em inglês).

**Do zero ao jogo com IA (modelos + orquestração + agentes):** [docs/ZERO_TO_GAME_AI_PT.md](docs/ZERO_TO_GAME_AI_PT.md) · [English](docs/ZERO_TO_GAME_AI.md).

### Formas de instalação

| Forma | Quando usar |
|-------|-------------|
| **Scripts na raiz** (`./install.sh`, `.\install.ps1`, `install.bat`) | Recomendado: prepara dependências do instalador (ex. Rich), cria `.venv` por projeto e instala em modo editável. |
| **`gamedev-install`** | Depois de `pip install -e Shared/` (ou com `PYTHONPATH` a apontar para `Shared/src`): mesmo registry que os scripts, útil em CI ou quando já tens o pacote Shared. |
| **Instalador local do projeto** (`<Projeto>/scripts/install.sh` ou `python scripts/installer.py`) | Atalho quando já estás dentro da pasta do projeto; **não** confundir com `GameDev/install.sh` da raiz (ver [docs/INSTALLING_PT.md](docs/INSTALLING_PT.md)). |
| **Manual / pipelines** | `python -m venv .venv` + `pip install -e .` por pasta; ver READMEs e secções «Manual» — para debugging ou CI sem o wrapper unificado. |

Variável útil: **`PYTHON_CMD`** (ou `--python` no instalador) para forçar o interpretador (por defeito `python3` em Unix, `python` no Windows nos scripts).

### Instalador unificado (recomendado)

O monorepo inclui um instalador unificado que instala qualquer ferramenta registada:

```bash
# Linux/macOS
./install.sh --list                     # Listar ferramentas disponíveis
./install.sh materialize                # Instalar Materialize (Rust)
./install.sh text2d                     # Cria Text2D/.venv se necessário; instala no venv do projecto
./install.sh texture2d                  # Idem (Texture2D/.venv)
./install.sh skymap2d                   # Skymap2D (skymaps equirectangular; sem GPU)
./install.sh text2sound                 # Text2Sound (requer CUDA; instala PyTorch)
./install.sh text3d                     # Text3D (Text2D + Hunyuan; nvdiffrast para Paint)
./install.sh gameassets                 # GameAssets (batch; orquestra outras CLIs)
./install.sh part3d                     # Part3D (Hunyuan3D-Part; torch-scatter/cluster)
./install.sh paint3d                    # Paint3D (textura + nvdiffrast)
./install.sh rigging3d                  # Rigging3D (UniRig empacotado + PyTorch/CUDA via instalador)
./install.sh animator3d                 # Animator3D (bpy / animação; sem PyTorch)
./install.sh terrain3d                   # Terrain3D (terreno IA; CUDA GPU)
./install.sh all                        # Instalar tudo

# Windows PowerShell (recomendado no Windows: o script detecta `python` e passa-o ao instalador)
.\install.ps1 --list
.\install.ps1 materialize
.\install.ps1 text2d
.\install.ps1 texture2d
.\install.ps1 skymap2d
.\install.ps1 text2sound
.\install.ps1 text3d
.\install.ps1 gameassets
.\install.ps1 part3d
.\install.ps1 paint3d
.\install.ps1 rigging3d
.\install.ps1 animator3d
.\install.ps1 terrain3d
.\install.ps1 all

# Windows CMD (idem: `install.bat` passa o interpretador ao instalador)
install.bat materialize
```

Equivalente com o pacote Shared instalado: `gamedev-install text2d`, `gamedev-install all`, etc. (lista: `gamedev-install --list`).

Opções do instalador unificado:

| Opção | Descrição |
|-------|-----------|
| `--action {install,uninstall,reinstall}` | Acção a executar (default: install) |
| `--use-venv` | Legado (opcional); o instalador **cria** sempre `projecto/.venv` se não existir e instala aí |
| `--skip-deps` | Não instalar dependências de sistema |
| `--skip-models` | Não configurar modelos/pesos |
| `--force` | Forçar reinstalação |
| `--prefix PATH` | Prefixo de instalação (default: ~/.local) |
| `--python CMD` | Comando Python (default: python3) |
| `--list` | Listar ferramentas disponíveis |
| `--skip-env-config` | Text3D: não escrever `~/.config/text3d/env.sh` (ou `env.bat` no Windows) |

### Instalação manual

```bash
# 1. Instalar Shared (obrigatório para todos os projectos Python)
cd Shared && pip install -e . && cd ..

# 2. Text2D (imagem)
cd Text2D && ./scripts/setup.sh && source .venv/bin/activate && text2d --help

# 3. Text3D (3D; depende do Text2D como pacote local — ver Text3D/README)
cd ../Text3D
python -m venv .venv && source .venv/bin/activate
pip install -r config/requirements.txt && pip install -e .
text3d --help

# 4. Part3D (partes semânticas; torch-scatter/cluster após PyTorch — ver Part3D/README)
cd ../Part3D && python -m venv .venv && source .venv/bin/activate && pip install -e . && part3d --help

# 5. Paint3D (Hunyuan3D-Paint 2.1; código vendored em Paint3D/src/paint3d/hy3dpaint/ + nvdiffrast — ver Paint3D/docs/PAINT_SETUP.md)
cd ../Paint3D
python -m venv .venv && source .venv/bin/activate
pip install torch torchvision
pip install -r config/requirements.txt && pip install -e .
pip install git+https://github.com/NVlabs/nvdiffrast.git --no-build-isolation
paint3d --help

# 6. GameAssets (batch; Text2D/Text3D na PATH ou TEXT2D_BIN/TEXT3D_BIN; Texture2D opcional TEXTURE2D_BIN; Materialize opcional MATERIALIZE_BIN)
cd ../GameAssets && chmod +x scripts/setup.sh && ./scripts/setup.sh && source .venv/bin/activate && gameassets --help

# 7. Texture2D (texturas seamless via HF API; sem PyTorch local)
cd ../Texture2D && chmod +x scripts/setup.sh && ./scripts/setup.sh && source .venv/bin/activate && texture2d --help

# 8. Skymap2D (skymaps equirectangular 360° via HF API; sem PyTorch local)
cd ../Skymap2D && chmod +x scripts/setup.sh && ./scripts/setup.sh && source .venv/bin/activate && skymap2d --help

# 9. Text2Sound (text-to-audio; Stable Audio Open 1.0; requer CUDA)
cd ../Text2Sound && chmod +x scripts/setup.sh && ./scripts/setup.sh && source .venv/bin/activate && text2sound --help

# 10. Rigging3D (GPU CUDA; Python 3.11; dependências pesadas — preferir ./install.sh rigging3d)
cd ../Rigging3D && pip install -e ".[inference,dev]" && rigging3d --help

# 11. Animator3D (animação; venv com Python 3.13 + bpy — ver Animator3D/README; Windows: py -3.13 -m venv .venv)
cd ../Animator3D && python3.13 -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]" && animator3d --help

# 12. Materialize (Rust — requer cargo)
cd ../Materialize && ./install.sh
```

Instruções completas: [docs/INSTALLING_PT.md](docs/INSTALLING_PT.md), [docs/NEW_TOOLS_PT.md](docs/NEW_TOOLS_PT.md) (registar novas ferramentas no monorepo), [Shared/README_PT.md](Shared/README_PT.md), e os READMEs de cada pasta (`README_PT.md` por pacote quando existir).

## Licenças

| Componente | Licença | Nota |
|-----------|---------|------|
| Código do monorepo (Text2D, Text3D, Part3D, Paint3D, Texture2D, Skymap2D, Text2Sound, Rigging3D, Animator3D, GameAssets, GameDevLab, Terrain3D, Shared) | MIT | Ver `LICENSE` em cada pasta |
| Materialize CLI (Rust) | MIT | [Materialize/LICENSE](Materialize/LICENSE) |
| FLUX.2 Klein 4B (oficial, BF16) | Apache 2.0 | [black-forest-labs/FLUX.2-klein-4B](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B) — uso comercial permitido segundo o model card; mais VRAM que o SDNQ |
| FLUX.2 Klein 4B SDNQ (default Text2D) | FLUX Non-Commercial (metadata HF) | [Disty0/FLUX.2-klein-4B-SDNQ-4bit-dynamic](https://huggingface.co/Disty0/FLUX.2-klein-4B-SDNQ-4bit-dynamic) declara `flux-non-commercial-license`; **não** é o mesmo regime que o checkpoint oficial Apache 2.0. Para produto comercial, prefira `TEXT2D_MODEL_ID=black-forest-labs/FLUX.2-klein-4B` ou acordo com a BFL |
| Hunyuan3D-2.1 (shape + paint, Text3D + Paint3D) | Tencent Hunyuan Community License | [tencent/Hunyuan3D-2.1](https://huggingface.co/tencent/Hunyuan3D-2.1) — lê o `LICENSE` no repositório: restrições de território (ex.: UE, Reino Unido, Coreia do Sul), política de uso aceitável e obrigações em cadeia. Código: [Hunyuan3D-2.1](https://github.com/Tencent-Hunyuan/Hunyuan3D-2.1); pesos shape `hunyuan3d-dit-v2-1` (SDNQ INT4), pesos paint `hunyuan3d-paintpbr-v2-1` |
| Stable Audio Open 1.0 / Open Small (Text2Sound) | Stability AI Community License | [stabilityai/stable-audio-open-1.0](https://huggingface.co/stabilityai/stable-audio-open-1.0), [stabilityai/stable-audio-open-small](https://huggingface.co/stabilityai/stable-audio-open-small) — modelos **gated** (aceitar no Hub); uso comercial gratuito com teto de receita anual (ver `LICENSE.md` no repo, atualmente ~USD 1M; alterações: [stability.ai/license](https://stability.ai/license)) |
| Flux-Seamless-Texture-LoRA (Texture2D) | Apache 2.0 (metadata HF) | [gokaygokay/Flux-Seamless-Texture-LoRA](https://huggingface.co/gokaygokay/Flux-Seamless-Texture-LoRA) — LoRA sobre FLUX.1-dev: cumpre também os termos do modelo base e da API de inferência |
| Flux-LoRA-Equirectangular-v3 (Skymap2D) | Base FLUX.1 [dev] (NCL) + card HF | [MultiTrickFox/Flux-LoRA-Equirectangular-v3](https://huggingface.co/MultiTrickFox/Flux-LoRA-Equirectangular-v3) — sem SPDX no README; modelo base [FLUX.1-dev](https://huggingface.co/black-forest-labs/FLUX.1-dev) está sob licença não comercial BFL; origem Civitai no card |
| UniRig (código em `Rigging3D/…/unirig/`) | MIT | [VAST-AI-Research/UniRig](https://github.com/VAST-AI-Research/UniRig) · [THIRD_PARTY.md](Rigging3D/THIRD_PARTY.md) |
| UniRig (pesos HF) | MIT (vários mirrors listam MIT) | [VAST-AI/UniRig](https://huggingface.co/VAST-AI/UniRig) — confirma no README/`LICENSE` do snapshot que usas; [exemplo com LICENSE MIT](https://huggingface.co/apozz/UniRig-safetensors) |

> **Atenção:** os pesos têm licenças próprias. **Inference API** (Texture2D, Skymap2D): além do modelo, aplicam-se [termos Hugging Face](https://huggingface.co/terms-of-service) e políticas da API. **Não** redistribuir checkpoints sem cumprir a licença e atribuições do autor. Shap-E (`openai/shap-e`) em scripts legados Text3D exige aceitar termos no Hub.

## Variáveis de Ambiente

O monorepo usa variáveis de ambiente para localizar binários e configurar comportamento:

| Variável | Usada por | Descrição |
|----------|-----------|-----------|
| `TEXT2D_BIN` | GameAssets | Caminho para o binário `text2d` (se não estiver no `PATH`) |
| `TEXT3D_BIN` | GameAssets | Caminho para o binário `text3d` |
| `TEXTURE2D_BIN` | GameAssets | Caminho para o binário `texture2d` |
| `TEXT2SOUND_BIN` | GameAssets | Caminho para o binário `text2sound` |
| `MATERIALIZE_BIN` | GameAssets, Text3D | Caminho para o binário `materialize` |
| `TERRAIN3D_BIN` | GameAssets | Caminho para o binário `terrain3d` |
| `TEXT2D_MODEL_ID` | Text2D | Override do modelo HF para Text2D |
| `TEXTURE2D_MODEL_ID` | Texture2D | Override do modelo HF para Texture2D |
| `SKYMAP2D_MODEL_ID` | Skymap2D | Override do modelo HF para Skymap2D |
| `HF_TOKEN` | Text2Sound, Texture2D, Skymap2D | Token Hugging Face para APIs autenticadas |
| `HF_HOME` | Todos (Python) | Diretório de cache Hugging Face (defeito: `~/.cache/huggingface`) |
| `PYTORCH_CUDA_ALLOC_CONF` | Text2D, Text3D, GameAssets | Configuração de alocação CUDA (auto-definida se vazia) |
| `TEXT3D_ALLOW_SHARED_GPU` | Text3D | Permitir GPU partilhada com outros processos |
| `TEXT3D_GPU_KILL_OTHERS` | Text3D | Controlar terminação de processos GPU concorrentes |
| `TEXT3D_EXPORT_ROTATION_X_DEG` | Text3D | Rotação X ao exportar mesh (graus) |
| `PAINT3D_ALLOW_SHARED_GPU` | Paint3D | Permitir GPU partilhada com outros processos |
| `PAINT3D_GPU_KILL_OTHERS` | Paint3D | Controlar terminação de processos GPU concorrentes |
| `PAINT3D_MULTI_GPU` | Paint3D | **Obsoleto** — usar `--gpu-ids 0,1`. Variável de ambiente legada para dividir VAE entre GPUs |
| `RIGGING3D_ROOT` | Rigging3D | Raiz da árvore de inferência (por defeito: pacote incluído) |
| `RIGGING3D_PYTHON` | Rigging3D | Interpretador Python do ambiente de inferência |

## Desenvolvimento

### Ferramentas de qualidade

O monorepo usa ferramentas centralizadas para lint, formatação, testes e type-checking:

| Ferramenta | Âmbito | Config |
|------------|--------|--------|
| [**Ruff**](https://docs.astral.sh/ruff/) | Lint + format (Python) | `ruff.toml` (raiz) |
| [**MyPy**](https://mypy.readthedocs.io/) | Type-checking (Python) | `mypy.ini` (raiz) |
| [**Pytest**](https://pytest.org/) + **pytest-cov** | Testes + cobertura | `pyproject.toml` por pacote |
| [**Cargo Clippy**](https://doc.rust-lang.org/clippy/) | Lint (Rust) | via Makefile |
| [**Pre-commit**](https://pre-commit.com/) | Hooks de pré-commit | `.pre-commit-config.yaml` |
| [**GitHub Actions**](https://github.com/features/actions) | CI (lint + test + clippy) | `.github/workflows/ci.yml` |

### Makefile (GNU Make)

```bash
make help            # Listar todos os targets
make lint            # Ruff check + Cargo clippy
make fmt             # Ruff format + Cargo fmt
make fmt-check       # Verificar formatação sem alterar
make test            # Pytest em todos os pacotes + Cargo test
make test-shared     # Pytest só no Shared
make test-text2d     # Pytest só no Text2D
make typecheck       # MyPy no Shared/src
make check           # lint + fmt-check + typecheck + test (CI completo)
make clean           # Remover __pycache__, caches, builds
make install-hooks   # Instalar pre-commit hooks
```

> **Windows:** requer GNU Make (via Git Bash, MSYS2 ou WSL).

### Setup de desenvolvimento

```bash
# 1. Instalar pre-commit hooks
pip install pre-commit
make install-hooks

# 2. Instalar dependências de dev num pacote (exemplo: Shared)
cd Shared && pip install -e ".[dev]" && cd ..

# 3. Correr testes
make test-shared

# 4. Lint e format
make lint
make fmt
```

### pyproject.toml

Cada pacote Python tem um `pyproject.toml` (PEP 621) com metadata, dependências e config do pytest.
Os ficheiros `setup.py` existentes permanecem para compatibilidade com instaladores legados.

## Referências

Alguns componentes seguem o desenho de projectos externos:

- **[Materialize](Materialize/)** (CLI Rust): inspirado no [Materialize](https://github.com/BoundingBoxSoftware/Materialize) original da Bounding Box Software (Unity/Windows). Ver [`Materialize/README.md`](Materialize/README.md).
- **[VibeGame](VibeGame/)** (motor TypeScript): projecto upstream [dylanebert/VibeGame](https://github.com/dylanebert/vibegame). Ver [`VibeGame/README.md`](VibeGame/README.md).

## Contribuir

- Preferir commits pequenos e mensagens no estilo [Conventional Commits](https://www.conventionalcommits.org/).
- Ignorar ambientes virtuais e caches: o `.gitignore` na raiz alinha-se com os de cada subpasta.
- Correr `make check` antes de submeter PRs.
- Cada ferramenta tem o seu `pyproject.toml` com `[project.optional-dependencies] dev` — instala com `pip install -e ".[dev]"` antes de correr testes.
- **Documentação** por ferramenta: mantém o `README.md` e, quando existir, a pasta `docs/` atualizada.
