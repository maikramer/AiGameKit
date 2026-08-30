# AiGameKit

**Documentação:** [English (`README.md`)](README.md) · Português (esta página)

[![CI](https://github.com/maikramer/AiGameKit/actions/workflows/ci.yml/badge.svg)](https://github.com/maikramer/AiGameKit/actions)
[![Python 3.13](https://img.shields.io/badge/python-3.13-blue.svg)](https://www.python.org/downloads/)
[![Rust](https://img.shields.io/badge/rust-1.75+-orange.svg)](https://www.rust-lang.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](Text2D/LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

Monorepo com ferramentas de **texto→imagem**, **texto→3D**, **texto→áudio**, **texturas seamless (GPU local)** e **skymaps** (GPU local), **texturização PBR**, **rigging**, **animação** e **batch de assets**, partilhando a mesma base (`aigamekit-shared`), instalador unificado e documentação.

## Projetos

| Pasta | Descrição |
|-------|-----------|
| [**Shared**](Shared/) | Biblioteca partilhada (`aigamekit-shared`): logging, GPU, subprocess, instaladores, CLI. |
| [**Text2D**](Text2D/) | CLI **text-to-image** com FLUX (quantização SDNQ), orientada a GPU modesta. |
| [**Text3D**](Text3D/) | Pipeline **text-to-3D**: imagem 2D (via Text2D) → mesh GLB com Hunyuan3D-Omni (SDNQ INT4; controlos bbox/pose/point/voxel). Textura via Paint3D (opcional). |
| [**Paint3D**](Paint3D/) | **Texturização 3D**: Hunyuan3D-Paint 2.1 (PBR multivista) + Materialize PBR + Upscale IA (Real-ESRGAN). Standalone ou via Text3D. |
| [**Part3D**](Part3D/) | **Decomposição semântica de partes**: Hunyuan3D-Part (P3-SAM + X-Part). SDNQ + CPU offload para ~6 GB VRAM. |
| [**GameAssets**](GameAssets/) | **Batch de prompts/assets**: perfil + CSV → `text2d` ou `texture2d` + opcional `text3d`, rig, **Animator3D** (auto-detetado), **`gameassets dream`** (ideia → scaffold Vite). |
| [**Texture2D**](Texture2D/) | **Texturas 2D seamless** (tileable) via pattern-diffusion (GPU local) + PBR via Materialize. |
| [**Skymap2D**](Skymap2D/) | **Skymaps equirectangular 360°** — FLUX.1-dev + LoRA local na GPU (CUDA), skyboxes para game dev. |
| [**Text2Sound**](Text2Sound/) | CLI **text-to-audio** com Stable Audio 3 Small (música/SFX separados): áudio estéreo 44.1 kHz, presets para game dev. |
| [**Rigging3D**](Rigging3D/) | **rigging3d** — auto-rigging 3D com [**SkinTokens**](https://github.com/VAST-AI-Research/SkinTokens) (skeleton + skinning unificados autoregressivos, sucessor do UniRig); GPU CUDA; Python **3.13**, **bpy** 5.2 LTS. |
| [**Animator3D**](Animator3D/) | **animator3d** — **bpy** 5.2 LTS; Python **3.13**; clips procedimentais, **`game-pack`** (presets humanoid/creature/flying), export GLB após rigging. |
| [**Materialize**](Materialize/) | CLI **PBR maps** (Rust/wgpu): gera normal, AO, metallic, smoothness a partir de textura difusa. |
| [**AiGameKitLab**](AiGameKitLab/) | **Lab CLI**: debug 3D, bancos de quantização, profiling, otimização de pipeline. |
| [**Terrain3D**](Terrain3D/) | **terrain3d** — Geração de terreno por IA via modelos de difusão (terrain-diffusion; CUDA GPU). |
| [**VibeGame**](VibeGame/) | **vibegame** — motor 3D em TypeScript (ECS, Three.js, XML declarativo); **Bun** + **Vite**. Ver [VibeGame/README.md](VibeGame/README.md). |

Cada projeto tem o seu próprio `README`, `setup`, requisitos e licença.

## Arquitectura

```
AiGameKit/
  Shared/           ← aigamekit-shared (pip): logging, GPU, subprocess, env, instaladores
  Text2D/           ← text2d (pip) — depende de Shared
  Text3D/           ← text3d (pip) — depende de Shared + Text2D; textura via Paint3D (opcional)
  Paint3D/           ← paint3d (pip) — depende de Shared; Hunyuan3D-2.1 hy3dpaint + Materialize PBR + Upscale
  Part3D/            ← part3d (pip) — depende de Shared; Hunyuan3D-Part (P3-SAM + X-Part)
  GameAssets/        ← gameassets (pip) — depende de Shared; chama text2d/texture2d/text3d via subprocess
  Texture2D/         ← texture2d (pip) — depende de Shared; pattern-diffusion local + PBR via Materialize
  Skymap2D/          ← skymap2d (pip) — depende de Shared; skymaps equirectangular (FLUX.1-dev + LoRA local)
  Text2Sound/        ← text2sound (pip) — depende de Shared; Stable Audio 3 Small (music/sfx)
  Rigging3D/         ← rigging3d (pip) — Shared; SkinTokens Py 3.13 + bpy 5.2 LTS
  Animator3D/        ← animator3d (pip) — Shared; Py 3.13 + bpy 5.2 LTS (animação)
  AiGameKitLab/        ← aigamekit-lab (pip) — depende de Shared; debug 3D, benches, profiling
  Terrain3D/        ← terrain3d (pip) — depende de Shared; geração de terreno por IA via difusão
  Materialize/       ← materialize-cli (cargo) — instalador Python usa Shared
  VibeGame/          ← vibegame (npm/Bun + Vite) — motor 3D no browser; standalone, não é pip
```

## Requisitos gerais

- **Python**: todas as ferramentas pedem **3.13** (cada `pyproject.toml` fixa `>=3.13,<3.14`); `bpy>=5.2.0` (LTS) para ferramentas de mesh. Ver README de cada pasta.
- **VibeGame** usa **Bun** e ferramentas compatíveis com **Node** (ver `VibeGame/package.json`); na raiz do repositório, `make test-vibegame` após instalar o Bun.
- **GPU** opcional no Text2D; no Text3D/Paint3D/Rigging3D, CUDA com VRAM suficiente é recomendado para tempos aceitáveis. **Texture2D** corre localmente em GPU CUDA (pattern-diffusion). **Skymap2D** corre localmente em GPU CUDA (FLUX.1-dev + LoRA). **GameAssets** só exige GPU se o perfil/linha invocar ferramentas locais (ex. text2d, text3d). **Multi-GPU:** a maioria das ferramentas com GPU aceita `--gpu-ids 0,1` para dividir pesos do modelo entre várias GPUs NVIDIA via accelerate; detecção de GPUs / VRAM livre via **NVML** (`aigamekit_shared.gpu`, dep `nvidia-ml-py`).
- Os **pesos dos modelos** (Hugging Face, etc.) têm licenças próprias — consulta os model cards antes de distribuir ou usar em produção.

## Arranque rápido

Guia completo em português: **[docs/INSTALLING_PT.md](docs/INSTALLING_PT.md)**. Versão em inglês: [docs/INSTALLING.md](docs/INSTALLING.md).

**Pipeline jogo (GameAssets → Vite / VibeGame, pastas, handoff GLB):** [docs/MONOREPO_GAME_PIPELINE.md](docs/MONOREPO_GAME_PIPELINE.md) (documento em inglês).

**Lições Hunyuan shape / repair / Part3D (faces vs X-Part, pés de elefante, finos soldados):** [docs/HUNYUAN_MESH_AND_PARTS_LESSONS_PT.md](docs/HUNYUAN_MESH_AND_PARTS_LESSONS_PT.md).

**Hub de descobertas dos modelos** (VRAM, SDNQ, kernels, Omni, vramd, paint/sky/mesh): [docs/MODEL_FINDINGS.md](docs/MODEL_FINDINGS.md) · [docs/findings/](docs/findings/) · Omni [docs/OMNI_SHAPE_FINDINGS.md](docs/OMNI_SHAPE_FINDINGS.md) · benches [docs/KERNEL_OPTS_BENCH.md](docs/KERNEL_OPTS_BENCH.md).

**Compressão GLB (KTX2 + meshopt, `text3d finish`):** [docs/GLB_FINISH_COMPRESSION.md](docs/GLB_FINISH_COMPRESSION.md).

**Waves vramd no batch** (shape/paint + tools GPU opcionais): [docs/GAMEASSETS_UMS_BATCH.md](docs/GAMEASSETS_UMS_BATCH.md).

**Missão / premissas** (facilidade, automação, agent-first, VRAM=infra): [docs/mission/](docs/mission/README.md) · resumo em [AGENTS.md](AGENTS.md).

**Logging em ficheiro** (todas as tools Python + vramd → `~/.cache/aigamekit/logs/`): [docs/LOGGING_PT.md](docs/LOGGING_PT.md) · [English](docs/LOGGING.md).

**Testes** (piso ≥100/ferramenta, nomes das suites, regras CPU-first): [docs/TESTING_PT.md](docs/TESTING_PT.md) · [English](docs/TESTING.md).

**Do zero ao jogo com IA (modelos + orquestração + agentes):** [docs/ZERO_TO_GAME_AI_PT.md](docs/ZERO_TO_GAME_AI_PT.md) · [English](docs/ZERO_TO_GAME_AI.md).

### Formas de instalação

| Forma | Quando usar |
|-------|-------------|
| **One-liner (Clified, sem clone)** | Máquina limpa — instala o motor Clified + ferramenta AiGameKit via [catálogo remoto](https://github.com/maikramer/clified-catalog). |
| **Scripts na raiz** (`./install.sh`, `.\install.ps1`, `install.bat`) | Com clone: [Clified](https://pypi.org/project/clified/) via PyPI + `tools.yaml` deste repo. |
| **`aigamekit-install`** | Depois de `pip install -e Shared/` (ou `PYTHONPATH` → `Shared/src`): mesmo registry que os scripts. |
| **Instalador local** (`python scripts/installer.py` numa pasta de ferramenta) | Atalho dentro do projecto — **não** confundir com `AiGameKit/install.sh` da raiz. |
| **Manual / pipelines** | `venv` + `pip install -e .` por pasta — debugging ou CI. |

Variável útil: **`PYTHON_CMD`** (ou `--python` no instalador).

### One-liner (Clified / sem clone)

**Linux / macOS:**

```bash
curl -fsSL https://raw.githubusercontent.com/maikramer/clified/main/install.sh | bash -s -- --get text2d
curl -fsSL https://raw.githubusercontent.com/maikramer/clified/main/install.sh | bash -s -- --get materialize
curl -fsSL https://raw.githubusercontent.com/maikramer/clified/main/install.sh | bash -s -- --get aigamekit   # todas
```

**Windows (PowerShell):**

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/maikramer/clified/main/install.ps1))) --get text2d
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/maikramer/clified/main/install.ps1))) --get materialize
```

Chaves do catálogo = entradas em `tools.yaml` (`text2d`, `text3d`, `materialize`, `vibegame`, …). Lista: `clified-install --catalog`.

### Instalador unificado (a partir de clone)

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
./install.sh paint3d                    # Paint3D (textura + nvdiffrast)
./install.sh rigging3d                  # Rigging3D (SkinTokens + PyTorch/CUDA via instalador)
./install.sh animator3d                 # Animator3D (bpy / animação; sem PyTorch)
./install.sh aigamekitlab                 # AiGameKitLab (debug 3D, benches, profiling)
./install.sh terrain3d                  # Terrain3D (terreno IA; CUDA GPU)
./install.sh rocks3d                    # Rocks3D (rochas procedurais)
./install.sh vibegame                   # VibeGame (Bun + motor 3D Vite)
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
.\install.ps1 paint3d
.\install.ps1 rigging3d
.\install.ps1 animator3d
.\install.ps1 aigamekitlab
.\install.ps1 terrain3d
.\install.ps1 rocks3d
.\install.ps1 vibegame
.\install.ps1 all

# Windows CMD (idem: `install.bat` passa o interpretador ao instalador)
install.bat materialize
```

Equivalente com o pacote Shared instalado: `aigamekit-install text2d`, `aigamekit-install all`, etc. (lista: `aigamekit-install --list`).

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

# 4. Paint3D (Hunyuan3D-Paint 2.1; código vendored em Paint3D/src/paint3d/hy3dpaint/ + nvdiffrast — ver Paint3D/docs/PAINT_SETUP.md)
cd ../Paint3D
python -m venv .venv && source .venv/bin/activate
pip install torch torchvision
pip install -r config/requirements.txt && pip install -e .
pip install git+https://github.com/NVlabs/nvdiffrast.git --no-build-isolation
paint3d --help

# 5. GameAssets (batch; Text2D/Text3D na PATH ou TEXT2D_BIN/TEXT3D_BIN; Texture2D opcional TEXTURE2D_BIN; Materialize opcional MATERIALIZE_BIN)
cd ../GameAssets && chmod +x scripts/setup.sh && ./scripts/setup.sh && source .venv/bin/activate && gameassets --help

# 6. Texture2D (texturas seamless via pattern-diffusion; GPU local + PBR via Materialize)
cd ../Texture2D && chmod +x scripts/setup.sh && ./scripts/setup.sh && source .venv/bin/activate && texture2d --help

# 7. Skymap2D (skymaps equirectangular 360°; FLUX.1-dev + LoRA local)
cd ../Skymap2D && chmod +x scripts/setup.sh && ./scripts/setup.sh && source .venv/bin/activate && skymap2d --help

# 8. Text2Sound (text-to-audio; Stable Audio 3 Small music/sfx; requer CUDA)
cd ../Text2Sound && chmod +x scripts/setup.sh && ./scripts/setup.sh && source .venv/bin/activate && text2sound --help

# 9. Rigging3D (GPU CUDA; Python 3.13; SkinTokens — preferir ./install.sh rigging3d)
cd ../Rigging3D && pip install -e ".[inference,dev]" && rigging3d --help

# 10. Animator3D (animação; venv com Python 3.13 + bpy — ver Animator3D/README; Windows: py -3.13 -m venv .venv)
cd ../Animator3D && python3.13 -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]" && animator3d --help

# 11. Materialize (Rust — requer cargo)
cd ../Materialize && ./install.sh
```

Instruções completas: [docs/INSTALLING_PT.md](docs/INSTALLING_PT.md) (incl. registo de novas ferramentas via `tools.yaml`), [Shared/README_PT.md](Shared/README_PT.md), e os READMEs de cada pasta (`README_PT.md` por pacote quando existir).

## Licenças

| Componente | Licença | Nota |
|-----------|---------|------|
| Código do monorepo (Text2D, Text3D, Paint3D, Texture2D, Skymap2D, Text2Sound, Rigging3D, Animator3D, GameAssets, AiGameKitLab, Terrain3D, Shared) | MIT | Ver `LICENSE` em cada pasta |
| Materialize CLI (Rust) | MIT | [Materialize/LICENSE](Materialize/LICENSE) |
| FLUX.2 Klein 4B (oficial, BF16) | Apache 2.0 | [black-forest-labs/FLUX.2-klein-4B](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B) — uso comercial permitido segundo o model card; mais VRAM que o SDNQ |
| FLUX.2 Klein (default Text2D: base fp16 + SDNQ runtime) | 4B: Apache 2.0 · 9B: **gated** (aceitar termos no Hub) | O Text2D carrega a base oficial [4B](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B) / [9B](https://huggingface.co/black-forest-labs/FLUX.2-klein-9B) e aplica quantização SDNQ **em runtime** — sem checkpoint pré-quantizado por defeito. Mirrors pré-quantizados [Disty0](https://huggingface.co/Disty0/FLUX.2-klein-4B-SDNQ-4bit-dynamic) são opcionais via `TEXT2D_MODEL_ID` (declaram `flux-non-commercial-license`) |
| Hunyuan3D-Omni (shape Text3D) | Tencent Hunyuan Community License | [tencent/Hunyuan3D-Omni](https://huggingface.co/tencent/Hunyuan3D-Omni) — lê o `LICENSE` no repositório: restrições de território (ex.: UE, Reino Unido, Coreia do Sul), política de uso aceitável e obrigações. SDNQ INT4 em GPUs pequenas |
| Hunyuan3D-2.1 (paint Paint3D) | Tencent Hunyuan Community License | [tencent/Hunyuan3D-2.1](https://huggingface.co/tencent/Hunyuan3D-2.1) — pesos paint `hunyuan3d-paintpbr-v2-1`; mesmas restrições de território/uso. Código: [Hunyuan3D-2.1](https://github.com/Tencent-Hunyuan/Hunyuan3D-2.1) |
| Stable Audio 3 Small Music / SFX (Text2Sound) | Stability AI Community License | [stabilityai/stable-audio-3-small-music](https://huggingface.co/stabilityai/stable-audio-3-small-music), [stabilityai/stable-audio-3-small-sfx](https://huggingface.co/stabilityai/stable-audio-3-small-sfx) — modelos **gated** (aceitar no Hub); uso comercial com teto de receita anual (ver `LICENSE.md` + [stability.ai/license](https://stability.ai/license)); termos Gemma aplicam-se ao T5Gemma embutido. Legado [open-1.0](https://huggingface.co/stabilityai/stable-audio-open-1.0) / [open-small](https://huggingface.co/stabilityai/stable-audio-open-small) via aliases `--model` |
| Stable Diffusion 1.5 (default Texture2D) + pattern-diffusion (opcional) | SD1.5: CreativeML Open RAIL-M · pattern-diffusion: Apache 2.0 | O default é [stable-diffusion-v1-5/stable-diffusion-v1-5](https://huggingface.co/stable-diffusion-v1-5/stable-diffusion-v1-5) (circular padding, sem LoRA); [Arrexel/pattern-diffusion](https://huggingface.co/Arrexel/pattern-diffusion) (fine-tune de SD2-base em 6,8M padrões tileable) via `TEXTURE2D_MODEL_ID` |
| Flux-LoRA-Equirectangular-v3 (Skymap2D) | Base FLUX.1 [dev] (NCL) + card HF | [MultiTrickFox/Flux-LoRA-Equirectangular-v3](https://huggingface.co/MultiTrickFox/Flux-LoRA-Equirectangular-v3) — sem SPDX no README; modelo base [FLUX.1-dev](https://huggingface.co/black-forest-labs/FLUX.1-dev) está sob licença não comercial BFL; origem Civitai no card |
| SkinTokens (código em `Rigging3D/…/skintokens/`) | MIT | [VAST-AI-Research/SkinTokens](https://github.com/VAST-AI-Research/SkinTokens) — sucessor do UniRig · [THIRD_PARTY.md](Rigging3D/THIRD_PARTY.md) |
| SkinTokens (pesos HF) | MIT | [VAST-AI/SkinTokens](https://huggingface.co/VAST-AI/SkinTokens) — descarregados automaticamente no primeiro run (~1,6 GB) |

> **Atenção:** os pesos têm licenças próprias. **Não** redistribuir checkpoints sem cumprir a licença e atribuições do autor. Shap-E (`openai/shap-e`) em scripts legados Text3D exige aceitar termos no Hub.

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
| `TEXTURE2D_MODEL_ID` | Texture2D | Override do modelo HF para Texture2D (default `stable-diffusion-v1-5/stable-diffusion-v1-5`) |
| `SKYMAP2D_MODEL_ID` | Skymap2D | Override do LoRA HF (default `MultiTrickFox/Flux-LoRA-Equirectangular-v3`) |
| `SKYMAP2D_BASE_MODEL_ID` | Skymap2D | Override do base FLUX.1-dev (default `Disty0/FLUX.1-dev-SDNQ-uint4-svd-r32`; o oficial `black-forest-labs/FLUX.1-dev` é gated) |
| `HF_TOKEN` | Text2Sound, Skymap2D, Texture2D | Token Hugging Face para **download de modelos gated** (aceitar termos no Hub primeiro) |
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
| `VRAMD_BIN` | Tools GPU | Path para `vramd` (vramd) |
| `VRAMD_AUTO_START` | Tools GPU | `0` desliga auto-start do vramd |
| `VRAMD_PRIORITY` | Tools GPU / GameAssets | Prioridade na fila: `interactive` \| `batch` |
| `AIGAMEKIT_ALLOW_LEGACY_SERVER` | Shared / tools | `1` = servers per-tool + `ensure_vram` legacy (default off) |
| `AIGAMEKIT_PREFER_MONOREPO` | Shared / GameAssets | Default `1`: `resolve_binary` prefere `<Tool>/.venv/bin` a wrappers stale |
| `AIGAMEKIT_LOG_DIR` | Todas as tools Python + vramd | Dir de logs diários (default `~/.cache/aigamekit/logs`) |
| `AIGAMEKIT_LOG_FILE` | Todas as tools Python + vramd | Path exacto do ficheiro de log |
| `AIGAMEKIT_LOG_TOOL` | Todas as tools Python + vramd | Nome da tool no ficheiro (auto CLI / `vramd`) |
| `AIGAMEKIT_LOG_LEVEL` | Todas as tools Python + vramd | Nível mín.: `DEBUG` \| `INFO` \| `WARN` \| `ERROR` |
| `AIGAMEKIT_FILE_LOG` | Todas as tools Python + vramd | `0` desliga; `1` força on (preciso sob pytest) |
| `AIGAMEKIT_NO_FILE_LOG` | Todas as tools Python + vramd | `1` desliga logging em ficheiro |

Logs: `~/.cache/aigamekit/logs/<tool>-YYYY-MM-DD.log` (vramd → `vramd-….log`). Guia: [docs/LOGGING_PT.md](docs/LOGGING_PT.md).

## Unified Model Server (vramd)

Todas as ferramentas GPU (Text2D, Text2Icon, Text3D, Paint3D, Part3D, Texture2D, Skymap2D, Text2Sound, Terrain3D) delegam a geração ao **Unified Model Server** — um supervisor único que detém a VRAM da máquina. Um socket (`~/.cache/aigamekit/model-server.sock`), um processo, inventário global de modelos, sem servers por-tool.

**Como funciona:**

1. As CLIs chamam `delegate_to_vramd` **antes** de qualquer preparação GPU in-process; o vramd auto-arranca no primeiro generate (desligar com `VRAMD_AUTO_START=0`).
2. Os jobs passam por `JobQueue` → `AffinityScheduler` → `WorkerPool` (`MAX_INFLIGHT=1` — uma geração de cada vez).
3. Cada backend é um **worker subprocess persistente** no venv da tool (JSONL stdin/stdout) — após editar código de tool, `vramd respawn <backend>` recarrega sem reiniciar o supervisor.
4. Prioridade na fila: `interactive` (CLI) > `batch` (GameAssets define `VRAMD_PRIORITY=batch`). Afinidade VRAM salta backends frios (≤3 cuts); evicção **peso + LRU** mantém a VRAM dentro de margens seguras.
5. O **`hw-auto`** preenche sinais de pico (preset SDNQ, memory-efficient) no payload vramd — sem flag `--low-vram` para o operador.

```bash
vramd start | stop | status | submit | queue | wait | cancel | flush | backends | preload | evict | reap | respawn | zero | stats | debug | bench | doctor
vramd status                    # backends + HOLDING/QUEUE
vramd queue                     # jobs + timings
vramd wait <job_id>             # bloqueia até o job terminar
vramd respawn <backend>         # recarrega código de tool editado no worker
```

Flags das tools: `--vramd-priority interactive|batch`, `--no-vramd`, `--vramd-stream`. WAL: `~/.cache/aigamekit/vramd-jobs.jsonl`. Guia completo: [`Vramd/README.md`](Vramd/README.md).

### Modelos & gates HF

| Ferramenta | Modelo(s) default | Gate HF | Notas |
|------------|-------------------|---------|-------|
| **Text2D** | [FLUX.2 Klein 4B](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B) (baixa VRAM) / [FLUX.2 Klein 9B](https://huggingface.co/black-forest-labs/FLUX.2-klein-9B) (alta VRAM) — base fp16 + **quantização SDNQ em runtime** | 9B: **gated** (aceitar termos no Hub); 4B: público | Override `TEXT2D_MODEL_ID`; hw-auto escolhe 4B abaixo de ~7,5 GB VRAM |
| **Text2Icon** | [Sana 600M 512px](https://huggingface.co/Efficient-Large-Model/Sana_600M_512px_diffusers) (default) / [Clark Air 1.6B 1.58-bit](https://huggingface.co/clark-labs/clark-air-sana-1.6b-1.58bit) (baixa VRAM) | não | pipeline [Sana 1600M 512px](https://huggingface.co/Efficient-Large-Model/Sana_1600M_512px_diffusers) |
| **Text3D** | [Hunyuan3D-Omni](https://huggingface.co/tencent/Hunyuan3D-Omni) shape (SDNQ INT4; controlos bbox/pose/point/voxel) + imagem de referência Text2D FLUX | não | Tencent Community License; BiRefNet p/ remoção de fundo |
| **Paint3D** | [Hunyuan3D-2.1](https://huggingface.co/tencent/Hunyuan3D-2.1) paint (`hunyuan3d-paintpbr-v2-1`) | não | + Real-ESRGAN (upscale opcional) |
| **Part3D** | [Hunyuan3D-Part](https://huggingface.co/tencent/Hunyuan3D-Part) (P3-SAM + X-Part) | não | Tencent Community License |
| **Texture2D** | [Stable Diffusion 1.5](https://huggingface.co/stable-diffusion-v1-5/stable-diffusion-v1-5) + circular padding | não | Override `TEXTURE2D_MODEL_ID` (ex.: [pattern-diffusion](https://huggingface.co/Arrexel/pattern-diffusion)) |
| **Skymap2D** | Base [FLUX.1-dev SDNQ uint4](https://huggingface.co/Disty0/FLUX.1-dev-SDNQ-uint4-svd-r32) + [Flux-LoRA-Equirectangular-v3](https://huggingface.co/MultiTrickFox/Flux-LoRA-Equirectangular-v3) | não (mirror); o oficial [FLUX.1-dev](https://huggingface.co/black-forest-labs/FLUX.1-dev) é **gated** | Override `SKYMAP2D_BASE_MODEL_ID` |
| **Text2Sound** | [Stable Audio 3 Small Music](https://huggingface.co/stabilityai/stable-audio-3-small-music) / [SFX](https://huggingface.co/stabilityai/stable-audio-3-small-sfx) | **gated** — aceitar termos no Hub + `HF_TOKEN` | Stability AI Community License (+ Gemma Terms no T5Gemma) |
| **Rigging3D** | [SkinTokens](https://huggingface.co/VAST-AI/SkinTokens) (TokenRig) | não | sucessor do UniRig; MIT |
| **Terrain3D** | [terrain-diffusion-30m](https://huggingface.co/xandergos/terrain-diffusion-30m) | não | vendored; rasters WorldClim descarregados automaticamente |

**Modelos gated** exigem aceitar termos no Hugging Face Hub (e `HF_TOKEN` definido) antes do download dos pesos. Tudo corre **localmente** — o Hub é apenas fonte de pesos, nunca uma API de inferência.

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
| [**GitHub Actions**](https://github.com/features/actions) | CI (lint + mypy + pytest matrix + Materialize + VibeGame Bun) | `.github/workflows/ci.yml` — armadilhas: [`docs/TESTING_PT.md`](docs/TESTING_PT.md) |

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
- **[VibeGame](VibeGame/)** (motor TypeScript): motor próprio, pacote `aigamekit-vibegame`. Ver [`VibeGame/README.md`](VibeGame/README.md).

## Contribuir

- Preferir commits pequenos e mensagens no estilo [Conventional Commits](https://www.conventionalcommits.org/).
- Ignorar ambientes virtuais e caches: o `.gitignore` na raiz alinha-se com os de cada subpasta.
- Correr `make check` antes de submeter PRs.
- Cada ferramenta tem o seu `pyproject.toml` com `[project.optional-dependencies] dev` — instala com `pip install -e ".[dev]"` antes de correr testes.
- **Documentação** por ferramenta: mantém o `README.md` e, quando existir, a pasta `docs/` atualizada.
