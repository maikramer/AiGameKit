# Instalação no monorepo GameDev

**Idioma:** [English (`INSTALLING.md`)](INSTALLING.md) · Português (esta página)

## Forma oficial

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

Pré-requisitos do **instalador**: Python **3.10+**, `pip`, e dependências em [`Shared/config/requirements.txt`](../Shared/config/requirements.txt) (ex.: Rich), instaladas automaticamente por [`install.sh`](../install.sh) antes de carregar o módulo unificado.

Variável útil: `PYTHON_CMD` — interpretador a usar (por defeito `python3`, ou `python` no Windows nos scripts).

---

## Ferramentas registadas

| Comando `./install.sh …` | Pasta | Tipo | Python mín. | Notas |
|--------------------------|-------|------|---------------|--------|
| `text2d` | Text2D | Python | 3.10 | PyTorch/CUDA recomendado |
| `text3d` | Text3D | Python | 3.8 | Depende de Text2D; nvdiffrast pós-venv |
| `gameassets` | GameAssets | Python | 3.10 | Sem PyTorch no pacote; `batch` orquestra CLIs (auto-deteta 3D, rig, parts, animate do manifest + perfil) |
| `text2sound` | Text2Sound | Python | 3.10 | PyTorch/CUDA |
| `texture2d` | Texture2D | Python | 3.10 | HF API; GPU local opcional |
| `skymap2d` | Skymap2D | Python | 3.10 | HF API |
| `rigging3d` | Rigging3D | Python | 3.11 | UniRig; extras de inferência **sempre** via unificado |
| `animator3d` | Animator3D | Python | 3.13 | `bpy` 5.1 |
| `part3d` | Part3D | Python | 3.10 | torch-scatter/cluster pós-venv |
| `paint3d` | Paint3D | Python | 3.10 | Código vendored em `Paint3D/src/paint3d/hy3dpaint/` + patches + Real-ESRGAN; modelos sob demanda via `huggingface_hub`; nvdiffrast pós-venv |
| `materialize` | Materialize | Rust | — | Requer `cargo`; binário em `~/.local/bin` por defeito |
| `vibegame` | VibeGame | Bun | — | Requer **Bun** e **Node** no PATH; `bun install` + `bun run build`; CLI `vibegame` em `~/.local/bin` |

Instalar tudo o que estiver presente no checkout: `./install.sh all`.

Detalhes técnicos: [`Shared/src/gamedev_shared/installer/registry.py`](../Shared/src/gamedev_shared/installer/registry.py).

**De assets em batch ao jogo no browser (pastas, handoff GLB, VibeGame):** [MONOREPO_GAME_PIPELINE.md](MONOREPO_GAME_PIPELINE.md) (documento em inglês).

---

## Não confundir dois `install.sh`

| Ficheiro | Função |
|----------|--------|
| **`GameDev/install.sh`** (raiz) | Instalador **unificado** de qualquer ferramenta (`gamedev_shared.installer.unified`). |
| **`<Projeto>/scripts/install.sh`** | Atalho local que apenas chama `scripts/installer.py` **desse** projeto (mesma lógica que o unificado quando equivalente). **Não** é o script da raiz. |

Preferência: usar sempre `./install.sh <nome>` **a partir da raiz**. O wrapper em `scripts/` existe para quem já está dentro da pasta do projecto.

Os projectos Text2D, Text3D e Texture2D expõem também `scripts/run_installer.sh` (implementação); `scripts/install.sh` delega para esse script por compatibilidade.

---

## Instalação manual / CI

Para pipelines ou debugging, podes criar `venv` e `pip install -e` em cada pasta; vê os READMEs por projecto e secções «Manual» ou `scripts/setup.sh` (conveniência de desenvolvimento: cria `.venv` e instala em modo editável — **não** substitui o contrato documentado acima para «instalação oficial»).

---

## Documentação por ferramenta

- **[Adicionar uma nova ferramenta ao monorepo](NEW_TOOLS_PT.md)** — registry, instalador unificado, Shared, GameAssets, CI, checklist. Versão EN: [NEW_TOOLS.md](NEW_TOOLS.md).
- [Shared/README_PT.md](../Shared/README_PT.md) — pacote `gamedev-shared`, `gamedev-install` · [EN](../Shared/README.md)
- READMEs por pacote: **PT** `README_PT.md` / **EN** `README.md` — [Text2D](../Text2D/), [Text3D](../Text3D/), [GameAssets](../GameAssets/), [Texture2D](../Texture2D/), [Skymap2D](../Skymap2D/), [Text2Sound](../Text2Sound/), [Rigging3D](../Rigging3D/), [Animator3D](../Animator3D/), [Part3D](../Part3D/), [Paint3D](../Paint3D/), [Materialize](../Materialize/) (Materialize: [README_PT.md](../Materialize/README_PT.md))
