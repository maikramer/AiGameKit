# Instalação no monorepo GameDev

**Idioma:** [English (`INSTALLING.md`)](INSTALLING.md) · Português (esta página)

## One-liner (Clified / sem clone)

Instala o motor Clified e uma ferramenta GameDev a partir do [catálogo remoto](https://github.com/maikramer/clified-catalog) **sem** clonar este repo:

**Linux / macOS:**

```bash
curl -fsSL https://raw.githubusercontent.com/maikramer/clified/main/install.sh | bash -s -- --get text2d
curl -fsSL https://raw.githubusercontent.com/maikramer/clified/main/install.sh | bash -s -- --get materialize
curl -fsSL https://raw.githubusercontent.com/maikramer/clified/main/install.sh | bash -s -- --get gamedev   # todas
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

Com o pacote `gamedev-shared` instalado (ou `PYTHONPATH` a apontar para `Shared/src`):

```bash
gamedev-install --list
gamedev-install text2d
```

**Pré-requisitos do instalador:** Python **3.13** recomendado para a maioria das ferramentas GPU (ver tabela); `pip`. [Clified](https://pypi.org/project/clified/) **≥ 0.8.1** é instalado automaticamente via PyPI quando ausente (`CLIFIED_MIN_VERSION`). User Scripts são prependidos ao PATH (sessão + persistente). O `uv` é instalado pelo Clified durante a instalação das ferramentas.

Variáveis úteis: `CLIFIED_TOOLS` (por defeito `GameDev/tools.yaml`), `PYTHON_CMD`, `CLIFIED_MIN_VERSION`, `CLIFIED_PERSIST_PATH=0` para não escrever `~/.profile` no Unix.

Variável útil: `PYTHON_CMD` — interpretador a usar (por defeito `python3`, ou `python` no Windows nos scripts).

---

## Ferramentas registadas

| Comando `./install.sh …` | Pasta | Tipo | Python mín. | Notas |
|--------------------------|-------|------|---------------|--------|
| `text2d` | Text2D | Python | 3.13 | PyTorch/CUDA; SDNQ FLUX |
| `text3d` | Text3D | Python | 3.13 | Depende de Text2D; nvdiffrast pós-venv |
| `gameassets` | GameAssets | Python | 3.13 | Batch + `dream`; orquestra CLIs |
| `gamedevlab` | GameDevLab | Python | 3.13 | Debug 3D, benches, profiling |
| `text2sound` | Text2Sound | Python | 3.13 | PyTorch/CUDA |
| `texture2d` | Texture2D | Python | 3.13 | GPU local ou HF API |
| `skymap2d` | Skymap2D | Python | 3.13 | HF Inference API |
| `terrain3d` | Terrain3D | Python | 3.13 | Terreno por difusão; CUDA |
| `rocks3d` | Rocks3D | Python | 3.13 | Rochas procedurais; sem PyTorch |
| `rigging3d` | Rigging3D | Python | 3.13 | UniRig; extras de inferência via instalador unificado |
| `animator3d` | Animator3D | Python | 3.13 | `bpy` 5.2 LTS |
| `paint3d` | Paint3D | Python | 3.13 | Hunyuan3D-Paint + nvdiffrast |
| `materialize` | Materialize | Rust | — | Requer `cargo`; binário em `~/.local/bin` |
| `vibegame` | VibeGame | Bun | — | Requer **Bun**; CLI `vibegame` → `~/.local/bin` |

Instalar tudo o que estiver presente no checkout: `./install.sh all` ou one-liner `--get gamedev`.

Detalhes técnicos: [`tools.yaml`](../tools.yaml) (registo Clified) e hooks em [`Shared/src/gamedev_shared/installer/clified_hooks.py`](../Shared/src/gamedev_shared/installer/clified_hooks.py).

**De assets em batch ao jogo no browser (pastas, handoff GLB, VibeGame):** [MONOREPO_GAME_PIPELINE.md](MONOREPO_GAME_PIPELINE.md) (documento em inglês).

---

## Não confundir dois `install.sh`

| Ficheiro | Função |
|----------|--------|
| **`GameDev/install.sh`** (raiz) | Delega ao **Clified** (`tools.yaml` + hooks no repo). |
| **`<Projeto>/scripts/install.sh`** | Atalho local **desse** projecto. **Não** é o script da raiz. |

Preferência: `./install.sh <nome>` **a partir da raiz**. O wrapper em `scripts/` existe para quem já está dentro da pasta do projecto.

---

## Instalação manual / CI

Para pipelines ou debugging, podes criar `venv` e `pip install -e` em cada pasta; vê os READMEs por projecto e secções «Manual» ou `scripts/setup.sh` (conveniência de desenvolvimento — **não** substitui o contrato de «instalação oficial» acima).

---

## Edições locais sem loops de reinstall

`./install.sh <tool>` já instala em modo **editável** (`pip install -e` + `.pth` → `src/`). Processos CLI novos lêem o código do checkout.

GameAssets / `resolve_binary` preferem `<Tool>/.venv/bin/<cli>` a wrappers stale em `~/.local/bin` quando o monorepo é detetado (default). Opt-out: `GAMEDEV_PREFER_MONOREPO=0`.

**Só precisas de refrescar quando:**

| Mudança | O que correr |
|---------|----------------|
| Nova dep / entry point / metadata no `pyproject.toml` | `./install.sh <tool>` ou `clified update <tool>` |
| Código usado por um **worker UMS** da tool já a correr | `ums respawn <backend>` (ou `ums respawn`) — apanha `*/src/` sem reiniciar o supervisor |
| Código do **ModelServer / protocolo worker** (`Shared/.../worker_*.py`) | `gamedev-model-server stop` (o próximo job auto-arranca) |
| Clone movido (wrappers com paths partidos) | `./install.sh <tool>` uma vez para reescrever `~/.local/bin` |

Edições Python normais em `*/src/` → guardar → re-correr o CLI / batch. Sem reinstall.
VRAM: UMS + hw-auto (sem `--low-vram` público); ver
[`ModelServer/README.md`](../ModelServer/README.md).

---

## Documentação por ferramenta

- **[Adicionar uma nova ferramenta ao monorepo](NEW_TOOLS_PT.md)** — registry, instalador unificado, Shared, GameAssets, CI, checklist.
- [Shared/README_PT.md](../Shared/README_PT.md) — `gamedev-shared`, `gamedev-install`
- [Text2D](../Text2D/), [Text3D](../Text3D/), [GameAssets](../GameAssets/), [Texture2D](../Texture2D/), [Skymap2D](../Skymap2D/), [Text2Sound](../Text2Sound/), [Rigging3D](../Rigging3D/), [Animator3D](../Animator3D/), [Paint3D](../Paint3D/), [Materialize](../Materialize/), [VibeGame](../VibeGame/), [GameDevLab](../GameDevLab/), [Terrain3D](../Terrain3D/), [Rocks3D](../Rocks3D/) — READMEs PT/EN por pasta.
