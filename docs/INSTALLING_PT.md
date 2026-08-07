# Instalação no monorepo AiGameKit

**Idioma:** [English (`INSTALLING.md`)](INSTALLING.md) · Português (esta página)

## One-liner (Clified / sem clone)

Instala o motor Clified e uma ferramenta AiGameKit a partir do [catálogo remoto](https://github.com/maikramer/clified-catalog) **sem** clonar este repo:

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

Lista de entradas: `clified-install --catalog` (após instalar o motor). Chaves = [`tools.yaml`](../tools.yaml) (`text2d`, `text3d`, `materialize`, `vibegame`, …).

### Gerir ferramentas instaladas (Clified ≥ 0.8)

Depois de instalar uma ferramenta, os subcomandos `clified` acompanham-na e gerem-na via um ficheiro de estado local (`~/.config/clified/state.json`):

```bash
clified list                       # ferramentas instaladas + estado ok/broken
clified search text                # filtra o catálogo remoto (marca [instalado])
clified update text2d              # git pull + refrescar deps (ou: clified update --all)
clified uninstall text2d --purge   # remove wrapper/venv/clone/receipt
clified get text2d@<ref>           # fixar num branch, tag ou commit SHA
clified doctor                     # diagnosticar receipts partidos / wrappers órfãos (--fix)
clified <cmd> --json               # saída machine-readable para qualquer um dos acima
```

Acrescenta `--json` para saída machine-readable. Ver `clified --help` e o [README do Clified](https://github.com/maikramer/clified#managing-installed-tools).

---

## Forma oficial (a partir de clone)

Na **raiz** do repositório (pasta que contém `Shared/`, `install.sh`, `.git`):

| Plataforma | Comando |
|------------|---------|
| Linux / macOS | `./install.sh <ferramenta>` |
| Windows PowerShell | `.\install.ps1 <ferramenta>` |
| Windows CMD | `install.bat <ferramenta>` |

Bootstrap: `scripts/install-bootstrap.{sh,ps1}` + `scripts/_bootstrap.{sh,ps1}` (vendored do Clified v0.9.0) — deteção de Python, instalação do `clified` via pip, bootstrap do `uv`.

Com o pacote `aigamekit-shared` instalado (ou `PYTHONPATH` a apontar para `Shared/src`):

```bash
aigamekit-install --list
aigamekit-install text2d
```

**Pré-requisitos do instalador:** Python **3.13** recomendado para a maioria das ferramentas GPU (ver tabela); `pip`. [Clified](https://pypi.org/project/clified/) **≥ 0.8.1** é instalado automaticamente via PyPI quando ausente (`CLIFIED_MIN_VERSION`). User Scripts são prependidos ao PATH (sessão + persistente). O `uv` é instalado pelo `install.sh` / `install.ps1` quando ausente (`pip install uv`); opt-out com `CLIFIED_SKIP_UV=1`.

Variáveis úteis: `CLIFIED_TOOLS` (por defeito `AiGameKit/tools.yaml`), `PYTHON_CMD`, `CLIFIED_MIN_VERSION`, `CLIFIED_PERSIST_PATH=0` para não escrever `~/.profile` no Unix.

Variável útil: `PYTHON_CMD` — interpretador a usar (por defeito `python3`, ou `python` no Windows nos scripts).

---

## Ferramentas registadas

| Comando `./install.sh …` | Pasta | Tipo | Python mín. | Notas |
|--------------------------|-------|------|---------------|--------|
| `text2d` | Text2D | Python | 3.13 | PyTorch/CUDA; SDNQ FLUX |
| `text2icon` | Text2Icon | Python | 3.13 | Sana Sprint 0.6B (NVlabs/Sana); BG transparente via rembg; PyTorch |
| `text3d` | Text3D | Python | 3.13 | Depende de Text2D; nvdiffrast pós-venv |
| `part3d` | Part3D | Python | 3.13 | Decomposição semântica (Hunyuan3D-Part: P3-SAM + X-Part); PyTorch |
| `gameassets` | GameAssets | Python | 3.13 | Batch + `dream`; orquestra CLIs |
| `modelserver` | ModelServer | Python | 3.13 | Unified Model Server (`aigamekit-model-server`/`ums`); supervisor VRAM |
| `aigamekitlab` | AiGameKitLab | Python | 3.13 | Debug 3D, benches, profiling |
| `text2sound` | Text2Sound | Python | 3.13 | PyTorch/CUDA |
| `texture2d` | Texture2D | Python | 3.13 | GPU local (SD1.5) |
| `skymap2d` | Skymap2D | Python | 3.13 | GPU local (FLUX.1-dev + LoRA) |
| `terrain3d` | Terrain3D | Python | 3.13 | Terreno por difusão; CUDA |
| `rocks3d` | Rocks3D | Python | 3.13 | Rochas procedurais; sem PyTorch |
| `rigging3d` | Rigging3D | Python | 3.13 | SkinTokens; extras de inferência via instalador unificado |
| `animator3d` | Animator3D | Python | 3.13 | `bpy` 5.2 LTS |
| `motion3d` | Motion3D | Python | 3.13 | Text-to-motion T2M-GPT; PyTorch + `bpy`; liga Animator3D (`cross_deps`) |
| `paint3d` | Paint3D | Python | 3.13 | Hunyuan3D-Paint + nvdiffrast |
| `materialize` | Materialize | Rust | — | Requer `cargo`; binário em `~/.local/bin` |
| `vibegame` | VibeGame | Bun | — | Requer **Bun**; CLI `vibegame` → `~/.local/bin` |

Instalar tudo o que estiver presente no checkout: `./install.sh all` ou one-liner `--get aigamekit`.

Detalhes técnicos: [`tools.yaml`](../tools.yaml) (registo Clified) e hooks em [`Shared/src/aigamekit_shared/installer/clified_hooks.py`](../Shared/src/aigamekit_shared/installer/clified_hooks.py).

Cada tool Python corre um post-install que mete o extra `[dev]` (pytest, pytest-cov,
ruff) no **próprio** venv — é aí que a suite corre (`make test-<tool>`). Entrada
Python em `tools.yaml` sem `post_install` / `custom_install` é bug
(`Shared/tests/test_dev_extras.py`).

**De assets em batch ao jogo no browser (pastas, handoff GLB, VibeGame):** [MONOREPO_GAME_PIPELINE.md](MONOREPO_GAME_PIPELINE.md) (documento em inglês).

---

## Mapa de dependências (Linux × Windows)

Tudo abaixo é **detetado ou instalado automaticamente** salvo indicação *manual*. Numa
máquina limpa os pré-requisitos reais são Python com pip, Node.js (compressão
KTX2/meshopt) e — por ferramenta — Rust (`materialize`) ou Bun (`vibegame`).

| Dependência | Para quê | Linux / macOS | Windows | Auto? |
|-------------|----------|---------------|---------|-------|
| Python 3.10+ com pip | motor de instalação (`clified`); tools GPU precisam de 3.13 | Debian/Ubuntu: `sudo apt install python3-full python3-venv`; macOS: `brew install python@3.13` | instalador python.org ou Anaconda (marcar *Add to PATH*) | detetado; instalar manualmente |
| `uv` | criação rápida de venvs de todas as tools Python | — | — | ✅ `install.sh` / `install.ps1` instala-o (pip) quando ausente; `CLIFIED_SKIP_UV=1` opt-out |
| `clified` | motor de instalação | — | — | ✅ scripts de bootstrap em `scripts/` (vendored do Clified) instalam-no via pip na primeira execução |
| Node.js (`npx`) | KTX2/UASTC + fallback meshopt (`@gltf-transform/cli`) | Debian/Ubuntu: `curl -fsSL https://deb.nodesource.com/setup_22.x \| sudo -E bash - && sudo apt install -y nodejs` (o apt do 24.04 traz 18.x — precisa de ≥ 20.12 para o rolldown do VibeGame); macOS: `brew install node` | `winget install OpenJS.NodeJS.LTS` | manual |
| KTX-Software `ktx` | UASTC → KTX2 (obrigatório para GLBs KTX2) | ✅ `./install.sh text3d` baixa o tarball → `~/.local/bin/ktx` | ✅ `./install.sh text3d` extrai o `ktx.exe` do instalador NSIS com 7-Zip (sem admin); sem 7-Zip, instalar `KTX-Software-4.4.2-Windows-x64.exe` manualmente | Linux ✅ / Windows ✅ com 7-Zip |
| meshopt (`libmeshoptimizer`) | compressão GLB nativa (bpy 5.2) | Debian/Ubuntu: `sudo apt install libmeshoptimizer-dev`; macOS: `brew install meshoptimizer` | não disponível nativamente → fallback automático para `@gltf-transform/cli` (precisa de Node.js) | fallback automático |
| Rust / cargo | `materialize` (mapas PBR) | `sudo apt install build-essential` (toolchain C) + `curl -sSf https://sh.rustup.rs \| sh` | rustup-init.exe de rustup.rs (inclui toolchain MSVC) | manual |
| Bun | `vibegame` (motor 3D no browser) | `sudo apt install unzip` + `curl -fsSL https://bun.sh/install \| bash` | `powershell -c "irm bun.sh/install.ps1 \| iex"` | manual |
| Driver NVIDIA + CUDA | todas as tools GPU (text2d/text3d/paint3d/…) | `nvidia-smi` tem de listar uma GPU | NVIDIA App / instalação do driver | manual |

**Ver o que falta na tua máquina:** `text3d doctor` (bpy/meshopt/npx/ktx) ·
`clified doctor` (receipts/wrappers) · `ums doctor` (UMS/VRAM).

### Máquina limpa — quickstart Linux

```bash
sudo apt install python3-full python3-pip python3-venv build-essential unzip
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt install -y nodejs   # Node ≥ 20.12 (o apt do 24.04 traz 18.x)
curl -fsSL https://sh.rustup.rs | sh                    # só para materialize
curl -fsSL https://bun.sh/install | bash                # só para vibegame
git clone <este repo> && cd AiGameKit
./install.sh all        # uv + clified + KTX-Software instalados automaticamente
text3d doctor           # confirma deps de compressão (meshopt/ktx/npx)
```

> O `python3-pip` é obrigatório para o bootstrap (ele valida `python3 -m pip --version`).
> O `build-essential` é a toolchain C exigida pelo cargo/rustup (`materialize`) e por
> alguns wheels; o `unzip` é exigido pelo instalador do Bun (`vibegame`).
> O Node tem de ser **≥ 20.12**: o `nodejs` do apt do Ubuntu 24.04 é 18.x, velho
> demais para o build rolldown do VibeGame (`node:util` `styleText`).

### Máquina limpa — quickstart Windows

```powershell
# 1) Python 3.13: instalador python.org (marcar "Add to PATH")
# 2) Node.js: winget install OpenJS.NodeJS.LTS
# 3) 7-Zip (opcional, permite KTX2 automático): winget install 7zip.7zip
git clone <este repo>; cd AiGameKit
.\install.ps1 all       # uv + clified instalados automaticamente
text3d doctor
```

> Sem 7-Zip no Windows, o KTX2 fica offline até instalar
> `KTX-Software-4.4.2-Windows-x64.exe` manualmente; o meshopt continua a funcionar via Node.js.

### Teste de instalação em máquina limpa (Docker, Ubuntu 24.04)

Teste completo ponta-a-ponta que simula uma máquina nova (sem Python, sem Node)
num container: pré-requisitos `apt` do quickstart → `./install.sh` para cada
tool → smoke dos CLIs → verificação das deps de compressão (`text3d doctor`) →
**inferência real**: cada tool gera um objeto de verdade (imagem, áudio, GLB,
mapas PBR, heightmap) e a cadeia 3D corre até ao fim
(`text3d` mesh → `paint3d` textura → `rigging3d` esqueleto → `animator3d` clips).
Demora ~1–2 h (instalação CPU de torch/cargo/bun + inferência GPU).

```bash
scripts/docker/ubuntu-clean-test.sh                  # 2 grupos, tudo
TEST_TOOLS="rocks3d materialize" scripts/docker/ubuntu-clean-test.sh  # subset rápido
SKIP_INFERENCE=1 scripts/docker/ubuntu-clean-test.sh # só instalação + smokes
```

- Corre em **2 grupos** (limite do layout de disco: o repo pode viver num disco
  separado): A = tools leves (`modelserver rocks3d texture2d text2icon
  text2sound skymap2d aigamekitlab materialize`), B = cadeia GPU/3D
  (`modelserver text2d text3d paint3d rigging3d animator3d terrain3d vibegame
  gameassets`).
- **Caches do host montados** para evitar downloads massivos:
  `~/.cache/huggingface` (modelos) e `~/.cache/uv` (wheels). A GPU é passada com
  `--gpus all` quando o host tem `nvidia-smi` (fallback a CPU só em erros de
  arranque — um teste que falha nunca re-corre sem GPU).
- O contexto de build é minimizado pelo `.dockerignore` da raiz; o `.git` é
  incluído (o UMS deteta a raiz do monorepo por `.git` + `Shared/`).
- Logs em `logs/ubuntu-clean-test/` (um ficheiro por etapa + logs por grupo
  preservados fora dos artefactos limpos); exit code 0 só quando todos os grupos
  têm zero FAILs.
- **Tolerâncias conhecidas numa GPU de 6 GB** (limites físicos, não bugs de
  instalação): `skymap2d` (o load do FLUX.1-dev SDNQ precisa de ~5.6 GiB; o
  display do laptop reserva ~0.4 GiB) e a stage de paint do `gameassets batch`
  (resíduo do cache torch do UMS entre stages). Ambos reportam WARN; cada tool
  individual passa.
- Ficheiros: `scripts/docker/Dockerfile.ubuntu-clean` (imagem) ·
  `ubuntu-clean-test-inner.sh` (o teste, corre dentro do container) ·
  `ubuntu-clean-test.sh` (wrapper do host).

---

## Não confundir dois `install.sh`

| Ficheiro | Função |
|----------|--------|
| **`AiGameKit/install.sh`** (raiz) | Delega ao **Clified** (`tools.yaml` + hooks no repo). |
| **`<Projeto>/scripts/install.sh`** | Atalho local **desse** projecto. **Não** é o script da raiz. |

Preferência: `./install.sh <nome>` **a partir da raiz**. O wrapper em `scripts/` existe para quem já está dentro da pasta do projecto.

---

## Instalação manual / CI

Para pipelines ou debugging, podes criar `venv` e `pip install -e` em cada pasta; vê os READMEs por projecto e secções «Manual» ou `scripts/setup.sh` (conveniência de desenvolvimento — **não** substitui o contrato de «instalação oficial» acima).

---

## Edições locais sem loops de reinstall

`./install.sh <tool>` já instala em modo **editável** (`pip install -e` + `.pth` → `src/`). Processos CLI novos lêem o código do checkout.

GameAssets / `resolve_binary` preferem `<Tool>/.venv/bin/<cli>` a wrappers stale em `~/.local/bin` quando o monorepo é detetado (default). Opt-out: `AIGAMEKIT_PREFER_MONOREPO=0`.

**Só precisas de refrescar quando:**

| Mudança | O que correr |
|---------|----------------|
| Nova dep / entry point / metadata no `pyproject.toml` | `./install.sh <tool>` ou `clified update <tool>` |
| Código usado por um **worker UMS** da tool já a correr | `ums respawn <backend>` (ou `ums respawn`) — apanha `*/src/` sem reiniciar o supervisor |
| Código do **ModelServer / protocolo worker** (`Shared/.../worker_*.py`) | `aigamekit-model-server stop` (o próximo job auto-arranca) |
| Clone movido (wrappers com paths partidos) | `./install.sh <tool>` uma vez para reescrever `~/.local/bin` |

Edições Python normais em `*/src/` → guardar → re-correr o CLI / batch. Sem reinstall.
VRAM: UMS + hw-auto (sem `--low-vram` público); ver
[`ModelServer/README.md`](../ModelServer/README.md).

---

## Documentação por ferramenta

- **Adicionar uma nova ferramenta ao monorepo** — regista em [`tools.yaml`](../tools.yaml) (registry Clified) + hooks em [`Shared/src/aigamekit_shared/installer/clified_hooks.py`](../Shared/src/aigamekit_shared/installer/clified_hooks.py); ver "Ferramentas registadas" acima e [`AGENTS.md`](../AGENTS.md).
- [Shared/README_PT.md](../Shared/README_PT.md) — `aigamekit-shared`, `aigamekit-install`
- [Text2D](../Text2D/), [Text3D](../Text3D/), [GameAssets](../GameAssets/), [Texture2D](../Texture2D/), [Skymap2D](../Skymap2D/), [Text2Sound](../Text2Sound/), [Rigging3D](../Rigging3D/), [Animator3D](../Animator3D/), [Paint3D](../Paint3D/), [Materialize](../Materialize/), [VibeGame](../VibeGame/), [AiGameKitLab](../AiGameKitLab/), [Terrain3D](../Terrain3D/), [Rocks3D](../Rocks3D/) — READMEs PT/EN por pasta.
