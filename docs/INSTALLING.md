# Installing in the GameDev monorepo

**Language:** English · [Português (`INSTALLING_PT.md`)](INSTALLING_PT.md)

## One-liner (Clified / no clone)

Install the Clified engine and a GameDev tool from the [remote catalog](https://github.com/maikramer/clified-catalog) without cloning this repo:

**Linux / macOS:**

```bash
curl -fsSL https://raw.githubusercontent.com/maikramer/clified/main/install.sh | bash -s -- --get text2d
curl -fsSL https://raw.githubusercontent.com/maikramer/clified/main/install.sh | bash -s -- --get materialize
curl -fsSL https://raw.githubusercontent.com/maikramer/clified/main/install.sh | bash -s -- --get gamedev   # all tools
```

**Windows (PowerShell):**

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/maikramer/clified/main/install.ps1))) --get text2d
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/maikramer/clified/main/install.ps1))) --get materialize
```

List catalog entries: `clified-install --catalog` (after the engine is installed). Keys match [`tools.yaml`](../tools.yaml) (`text2d`, `text3d`, `materialize`, `vibegame`, …).

### Managing installed tools (Clified ≥ 0.8)

Once a tool is installed, the `clified` subcommands track and manage it via a local state file (`~/.config/clified/state.json`):

```bash
clified list                       # installed tools + ok/broken status
clified search text                # filter the remote catalog (marks [instalado])
clified update text2d              # git pull + refresh deps (or: clified update --all)
clified uninstall text2d --purge   # remove wrapper/venv/clone/receipt
clified get text2d@<ref>           # pin to a branch, tag, or commit SHA
clified doctor                     # diagnose broken receipts / orphan wrappers (--fix)
clified <cmd> --json               # machine-readable output for any of the above
```

Add `--json` for machine-readable output. See `clified --help` and the [Clified README](https://github.com/maikramer/clified#managing-installed-tools).

---

## Official method (from clone)

At the **repository root** (folder containing `Shared/`, `install.sh`, `.git`):

| Platform | Command |
|----------|---------|
| Linux / macOS | `./install.sh <tool>` |
| Windows PowerShell | `.\install.ps1 <tool>` |
| Windows CMD | `install.bat <tool>` |

With `gamedev-shared` installed (or `PYTHONPATH` pointing to `Shared/src`):

```bash
gamedev-install --list
gamedev-install text2d
```

**Installer prerequisites:** Python **3.13** recommended for most GPU tools (see table); `pip`. [Clified](https://pypi.org/project/clified/) **≥ 0.8.1** is installed automatically via PyPI when missing (`CLIFIED_MIN_VERSION`). User Scripts are prepended to PATH (session + persistent). `uv` is bootstrapped by Clified during tool installs.

Useful env vars: `CLIFIED_TOOLS` (defaults to `GameDev/tools.yaml`), `PYTHON_CMD`, `CLIFIED_MIN_VERSION`, `CLIFIED_PERSIST_PATH=0` to skip writing `~/.profile` on Unix.

Useful variable: `PYTHON_CMD` — interpreter to use (default `python3`, or `python` on Windows in the scripts).

---

## Registered tools

| `./install.sh …` command | Folder | Type | Min Python | Notes |
|--------------------------|--------|------|------------|-------|
| `text2d` | Text2D | Python | 3.13 | PyTorch/CUDA; SDNQ FLUX |
| `text3d` | Text3D | Python | 3.13 | Depends on Text2D; nvdiffrast after venv |
| `gameassets` | GameAssets | Python | 3.13 | Batch + `dream`; orchestrates CLIs |
| `gamedevlab` | GameDevLab | Python | 3.13 | Debug 3D, benches, profiling |
| `text2sound` | Text2Sound | Python | 3.13 | PyTorch/CUDA |
| `texture2d` | Texture2D | Python | 3.13 | Local GPU or HF API |
| `skymap2d` | Skymap2D | Python | 3.13 | HF Inference API |
| `terrain3d` | Terrain3D | Python | 3.13 | Diffusion terrain; CUDA |
| `rocks3d` | Rocks3D | Python | 3.13 | Procedural rocks; no PyTorch |
| `rigging3d` | Rigging3D | Python | 3.13 | UniRig; inference extras via unified installer |
| `animator3d` | Animator3D | Python | 3.13 | `bpy` 5.2 LTS |
| `paint3d` | Paint3D | Python | 3.13 | Hunyuan3D-Paint + nvdiffrast |
| `materialize` | Materialize | Rust | — | Needs `cargo`; binary in `~/.local/bin` |
| `vibegame` | VibeGame | Bun | — | Needs **Bun**; CLI `vibegame` → `~/.local/bin` |

Install everything present in the checkout: `./install.sh all` or one-liner `--get gamedev`.

Technical details: [`tools.yaml`](../tools.yaml) (Clified registry) and hooks in [`Shared/src/gamedev_shared/installer/clified_hooks.py`](../Shared/src/gamedev_shared/installer/clified_hooks.py).

**From batch assets to a browser game (folder layout, GLB handoff, VibeGame):** [MONOREPO_GAME_PIPELINE.md](MONOREPO_GAME_PIPELINE.md).

---

## Do not confuse root vs project installers

| File | Role |
|------|------|
| **`GameDev/install.sh`** (root) | Delegates to **Clified** (`tools.yaml` + repo hooks). |
| **`<Project>/scripts/installer.py`** | Local shortcut for that project only. **Not** the root script. |

Prefer `./install.sh <name>` **from the repo root**. Per-project `scripts/installer.py` exists for people already inside a tool folder.

---

## Manual install / CI

For pipelines or debugging, you can create a `venv` and `pip install -e` in each folder; see per-project READMEs and “Manual” sections or `scripts/setup.sh` (dev convenience: creates `.venv` and editable install — **does not** replace the “official install” contract documented above).

---

## Local edits without reinstall loops

`./install.sh <tool>` already does **editable** installs (`pip install -e` + `.pth` → `src/`). New CLI processes read live source from the checkout.

GameAssets / `resolve_binary` prefer `<Tool>/.venv/bin/<cli>` over stale `~/.local/bin` wrappers when the monorepo is detected (default). Opt out: `GAMEDEV_PREFER_MONOREPO=0`.

**You still need a refresh only when:**

| Change | What to run |
|--------|-------------|
| New dependency / entry point / `pyproject.toml` metadata | `./install.sh <tool>` or `clified update <tool>` |
| Code used by a **running UMS tool worker** | `ums respawn <backend>` (or `ums respawn` for all) — picks up `*/src/` without restarting the supervisor |
| Code in **ModelServer / worker protocol** (`Shared/.../worker_*.py`) | `gamedev-model-server stop` (next job auto-starts) |
| Moved the clone (broken wrapper paths) | `./install.sh <tool>` once to rewrite `~/.local/bin` |

Normal Python edits under `*/src/` → save → re-run the CLI / batch. No reinstall.
VRAM path: UMS + hw-auto (no public `--low-vram`); see
[`ModelServer/README.md`](../ModelServer/README.md).

---

## Documentation per tool

- **[Adding a new tool to the monorepo](NEW_TOOLS.md)** — registry, unified installer, Shared, GameAssets, CI, checklist.
- [Shared/README.md](../Shared/README.md) — `gamedev-shared`, `gamedev-install`
- [Text2D/README.md](../Text2D/README.md), [Text3D/README.md](../Text3D/README.md), [GameAssets/README.md](../GameAssets/README.md), [Texture2D/README.md](../Texture2D/README.md), [Skymap2D/README.md](../Skymap2D/README.md), [Text2Sound/README.md](../Text2Sound/README.md), [Rigging3D/README.md](../Rigging3D/README.md), [Animator3D/README.md](../Animator3D/README.md), [Paint3D/README.md](../Paint3D/README.md), [Materialize/README.md](../Materialize/README.md), [VibeGame/README.md](../VibeGame/README.md), [GameDevLab/README.md](../GameDevLab/README.md), [Terrain3D/README.md](../Terrain3D/README.md), [Rocks3D/README.md](../Rocks3D/README.md)
