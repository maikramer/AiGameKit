# Installing in the AiGameKit monorepo

**Language:** English · [Português (`INSTALLING_PT.md`)](INSTALLING_PT.md)

## One-liner (Clified / no clone)

Install the Clified engine and a AiGameKit tool from the [remote catalog](https://github.com/maikramer/clified-catalog) without cloning this repo:

**Linux / macOS:**

```bash
curl -fsSL https://raw.githubusercontent.com/maikramer/clified/main/install.sh | bash -s -- --get text2d
curl -fsSL https://raw.githubusercontent.com/maikramer/clified/main/install.sh | bash -s -- --get materialize
curl -fsSL https://raw.githubusercontent.com/maikramer/clified/main/install.sh | bash -s -- --get aigamekit   # all tools
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

Bootstrap: `scripts/install-bootstrap.{sh,ps1}` + `scripts/_bootstrap.{sh,ps1}` (vendored from Clified v0.9.0) — Python detection, `clified` install via pip, `uv` bootstrap.

With `aigamekit-shared` installed (or `PYTHONPATH` pointing to `Shared/src`):

```bash
aigamekit-install --list
aigamekit-install text2d
```

**Installer prerequisites:** Python **3.13** recommended for most GPU tools (see table); `pip`. [Clified](https://pypi.org/project/clified/) **≥ 0.8.1** is installed automatically via PyPI when missing (`CLIFIED_MIN_VERSION`). User Scripts are prepended to PATH (session + persistent). `uv` is bootstrapped by `install.sh` / `install.ps1` when missing (`pip install uv`); opt out with `CLIFIED_SKIP_UV=1`.

Useful env vars: `CLIFIED_TOOLS` (defaults to `AiGameKit/tools.yaml`), `PYTHON_CMD`, `CLIFIED_MIN_VERSION`, `CLIFIED_PERSIST_PATH=0` to skip writing `~/.profile` on Unix.

Useful variable: `PYTHON_CMD` — interpreter to use (default `python3`, or `python` on Windows in the scripts).

---

## Registered tools

| `./install.sh …` command | Folder | Type | Min Python | Notes |
|--------------------------|--------|------|------------|-------|
| `text2d` | Text2D | Python | 3.13 | PyTorch/CUDA; SDNQ FLUX |
| `text2icon` | Text2Icon | Python | 3.13 | Sana Sprint 0.6B (NVlabs/Sana); rembg transparent BG; PyTorch |
| `text3d` | Text3D | Python | 3.13 | Depends on Text2D; nvdiffrast after venv |
| `part3d` | Part3D | Python | 3.13 | Semantic decomposition (Hunyuan3D-Part: P3-SAM + X-Part); PyTorch |
| `gameassets` | GameAssets | Python | 3.13 | Batch + `dream`; orchestrates CLIs |
| `modelserver` | ModelServer | Python | 3.13 | Unified Model Server (`aigamekit-model-server`/`ums`); supervisor VRAM |
| `aigamekitlab` | AiGameKitLab | Python | 3.13 | Debug 3D, benches, profiling |
| `text2sound` | Text2Sound | Python | 3.13 | PyTorch/CUDA |
| `texture2d` | Texture2D | Python | 3.13 | Local GPU (SD1.5) |
| `skymap2d` | Skymap2D | Python | 3.13 | Local GPU (FLUX.1-dev + LoRA) |
| `terrain3d` | Terrain3D | Python | 3.13 | Diffusion terrain; CUDA |
| `rocks3d` | Rocks3D | Python | 3.13 | Procedural rocks; no PyTorch |
| `rigging3d` | Rigging3D | Python | 3.13 | SkinTokens; inference extras via unified installer |
| `animator3d` | Animator3D | Python | 3.13 | `bpy` 5.2 LTS |
| `motion3d` | Motion3D | Python | 3.13 | Text-to-motion T2M-GPT; PyTorch + `bpy`; links Animator3D (`cross_deps`) |
| `paint3d` | Paint3D | Python | 3.13 | Hunyuan3D-Paint + nvdiffrast |
| `materialize` | Materialize | Rust | — | Needs `cargo`; binary in `~/.local/bin` |
| `vibegame` | VibeGame | Bun | — | Needs **Bun**; CLI `vibegame` → `~/.local/bin` |

Install everything present in the checkout: `./install.sh all` or one-liner `--get aigamekit`.

Technical details: [`tools.yaml`](../tools.yaml) (Clified registry) and hooks in [`Shared/src/aigamekit_shared/installer/clified_hooks.py`](../Shared/src/aigamekit_shared/installer/clified_hooks.py).

Every Python tool runs a post-install hook that adds its `[dev]` extra (pytest,
pytest-cov, ruff) to the tool's own venv, because that is where the suite runs
(`make test-<tool>`). A Python entry in `tools.yaml` without `post_install` or
`custom_install` is a bug — `Shared/tests/test_dev_extras.py` fails on it.

**From batch assets to a browser game (folder layout, GLB handoff, VibeGame):** [MONOREPO_GAME_PIPELINE.md](MONOREPO_GAME_PIPELINE.md).

---

## Dependency map (Linux × Windows)

Everything below is **detected or installed automatically** unless marked *manual*. On a
fresh machine the real prerequisites are Python with pip, Node.js (KTX2/meshopt
compression), and — per tool — Rust (`materialize`) or Bun (`vibegame`).

| Dependency | Used for | Linux / macOS | Windows | Auto? |
|------------|----------|---------------|---------|-------|
| Python 3.10+ with pip | installer engine (`clified`); GPU tools need 3.13 | Debian/Ubuntu: `sudo apt install python3-full python3-venv`; macOS: `brew install python@3.13` | python.org installer or Anaconda (tick *Add to PATH*) | detected; install manually |
| `uv` | fast venv creation for every Python tool | — | — | ✅ `install.sh` / `install.ps1` installs it (pip) when missing; `CLIFIED_SKIP_UV=1` opts out |
| `clified` | installer engine | — | — | ✅ bootstrap scripts in `scripts/` (vendored from Clified) install it via pip on first run |
| Node.js (`npx`) | KTX2/UASTC + meshopt fallback (`@gltf-transform/cli`) | Debian/Ubuntu: `curl -fsSL https://deb.nodesource.com/setup_22.x \| sudo -E bash - && sudo apt install -y nodejs` (apt 24.04 traz 18.x — precisa de ≥ 20.12 para o rolldown do VibeGame); macOS: `brew install node` | `winget install OpenJS.NodeJS.LTS` | manual |
| KTX-Software `ktx` | UASTC → KTX2 (required for KTX2 GLBs) | ✅ `./install.sh text3d` downloads the tarball → `~/.local/bin/ktx` | ✅ `./install.sh text3d` extracts `ktx.exe` from the NSIS installer via 7-Zip (no admin); without 7-Zip, install `KTX-Software-4.4.2-Windows-x64.exe` manually | Linux ✅ / Windows ✅ with 7-Zip |
| meshopt (`libmeshoptimizer`) | native GLB mesh compression (bpy 5.2) | Debian/Ubuntu: `sudo apt install libmeshoptimizer-dev`; macOS: `brew install meshoptimizer` | not available natively → automatic fallback to `@gltf-transform/cli` (needs Node.js) | fallback automatic |
| Rust / cargo | `materialize` (PBR maps) | `sudo apt install build-essential` (C toolchain) + `curl -sSf https://sh.rustup.rs \| sh` | rustup-init.exe from rustup.rs (includes MSVC toolchain) | manual |
| Bun | `vibegame` (browser 3D engine) | `sudo apt install unzip` + `curl -fsSL https://bun.sh/install \| bash` | `powershell -c "irm bun.sh/install.ps1 \| iex"` | manual |
| NVIDIA driver + CUDA | all GPU tools (text2d/text3d/paint3d/…) | `nvidia-smi` must list a GPU | NVIDIA App / driver install | manual |

**Check what your machine is missing:** `text3d doctor` (bpy/meshopt/npx/ktx) ·
`clified doctor` (receipts/wrappers) · `ums doctor` (UMS/VRAM).

### Fresh machine — Linux quickstart

```bash
sudo apt install python3-full python3-pip python3-venv build-essential unzip
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt install -y nodejs   # Node ≥ 20.12 (o apt do 24.04 traz 18.x)
curl -fsSL https://sh.rustup.rs | sh                    # only for materialize
curl -fsSL https://bun.sh/install | bash                # only for vibegame
git clone <this repo> && cd AiGameKit
./install.sh all        # uv + clified + KTX-Software installed automatically
text3d doctor           # confirm compression deps (meshopt/ktx/npx)
```

> `python3-pip` is required for the bootstrap (it validates `python3 -m pip --version`).
> `build-essential` is the C toolchain needed by cargo/rustup (`materialize`) and
> some wheels; `unzip` is required by the Bun installer (`vibegame`).
> Node must be **≥ 20.12**: the `nodejs` package on Ubuntu 24.04 is 18.x, too old
> for the rolldown build used by VibeGame (`node:util` `styleText`).

### Fresh machine — Windows quickstart

```powershell
# 1) Python 3.13: python.org installer (tick "Add to PATH")
# 2) Node.js: winget install OpenJS.NodeJS.LTS
# 3) 7-Zip (optional, enables automatic KTX2): winget install 7zip.7zip
git clone <this repo>; cd AiGameKit
.\install.ps1 all       # uv + clified installed automatically
text3d doctor
```

> Without 7-Zip on Windows, KTX2 stays offline until you install
> `KTX-Software-4.4.2-Windows-x64.exe` manually; meshopt still works via Node.js.

### Clean-machine install test (Docker, Ubuntu 24.04)

A full end-to-end test that simulates a fresh machine (no Python, no Node) in a
container: `apt` prerequisites from the quickstart → `./install.sh` for every
tool → CLI smoke tests → compression deps check (`text3d doctor`) → **real
inference**: every tool generates an actual object (image, audio, GLB, PBR
maps, terrain heightmap) and the 3D chain runs end to end
(`text3d` mesh → `paint3d` texture → `rigging3d` skeleton → `animator3d` clips).
It takes ~1–2 h (CPU torch/cargo/bun installs + GPU inference).

```bash
scripts/docker/ubuntu-clean-test.sh                  # 2 groups, everything
TEST_TOOLS="rocks3d materialize" scripts/docker/ubuntu-clean-test.sh  # quick subset
SKIP_INFERENCE=1 scripts/docker/ubuntu-clean-test.sh # installs + smokes only
```

- Runs in **2 groups** (disk-layout limit: the repo may live on a separate
  drive): A = light tools (`modelserver rocks3d texture2d text2icon text2sound
  skymap2d aigamekitlab materialize`), B = GPU/3D chain (`modelserver text2d
  text3d paint3d rigging3d animator3d terrain3d vibegame gameassets`).
- **Host caches are mounted** to avoid massive downloads: `~/.cache/huggingface`
  (models) and `~/.cache/uv` (wheels). GPU is passed through with `--gpus all`
  when the host has `nvidia-smi` (falls back to CPU only on container startup
  errors — a failed *test* never re-runs without GPU).
- Build context is minimized by the root `.dockerignore`; `.git` is included
  (the UMS detects the monorepo root by `.git` + `Shared/`).
- Logs land in `logs/ubuntu-clean-test/` (per-step files + per-group logs
  preserved outside the cleaned artifacts); exit code 0 only when every group
  has zero FAILs.
- **Known tolerances on a 6 GB GPU** (hardware limits, not install bugs):
  `skymap2d` (FLUX.1-dev SDNQ load needs ~5.6 GiB; the laptop display reserves
  ~0.4 GiB) and the `gameassets batch` paint stage (UMS torch cache residual
  between stages). Both are reported as WARN; every individual tool passes.
- Files: `scripts/docker/Dockerfile.ubuntu-clean` (image) ·
  `ubuntu-clean-test-inner.sh` (the test, runs inside the container) ·
  `ubuntu-clean-test.sh` (host wrapper).

---

## Do not confuse root vs project installers

| File | Role |
|------|------|
| **`AiGameKit/install.sh`** (root) | Delegates to **Clified** (`tools.yaml` + repo hooks). |
| **`<Project>/scripts/installer.py`** | Local shortcut for that project only. **Not** the root script. |

Prefer `./install.sh <name>` **from the repo root**. Per-project `scripts/installer.py` exists for people already inside a tool folder.

---

## Manual install / CI

For pipelines or debugging, you can create a `venv` and `pip install -e` in each folder; see per-project READMEs and “Manual” sections or `scripts/setup.sh` (dev convenience: creates `.venv` and editable install — **does not** replace the “official install” contract documented above).

---

## Local edits without reinstall loops

`./install.sh <tool>` already does **editable** installs (`pip install -e` + `.pth` → `src/`). New CLI processes read live source from the checkout.

GameAssets / `resolve_binary` prefer `<Tool>/.venv/bin/<cli>` over stale `~/.local/bin` wrappers when the monorepo is detected (default). Opt out: `AIGAMEKIT_PREFER_MONOREPO=0`.

**You still need a refresh only when:**

| Change | What to run |
|--------|-------------|
| New dependency / entry point / `pyproject.toml` metadata | `./install.sh <tool>` or `clified update <tool>` |
| Code used by a **running UMS tool worker** | `ums respawn <backend>` (or `ums respawn` for all) — picks up `*/src/` without restarting the supervisor |
| Code in **ModelServer / worker protocol** (`Shared/.../worker_*.py`) | `aigamekit-model-server stop` (next job auto-starts) |
| Moved the clone (broken wrapper paths) | `./install.sh <tool>` once to rewrite `~/.local/bin` |

Normal Python edits under `*/src/` → save → re-run the CLI / batch. No reinstall.
VRAM path: UMS + hw-auto (no public `--low-vram`); see
[`ModelServer/README.md`](../ModelServer/README.md).

---

## Documentation per tool

- **Adding a new tool to the monorepo** — register it in [`tools.yaml`](../tools.yaml) (Clified registry) + hooks in [`Shared/src/aigamekit_shared/installer/clified_hooks.py`](../Shared/src/aigamekit_shared/installer/clified_hooks.py); see "Registered tools" above and [`AGENTS.md`](../AGENTS.md).
- [Shared/README.md](../Shared/README.md) — `aigamekit-shared`, `aigamekit-install`
- [Text2D/README.md](../Text2D/README.md), [Text3D/README.md](../Text3D/README.md), [GameAssets/README.md](../GameAssets/README.md), [Texture2D/README.md](../Texture2D/README.md), [Skymap2D/README.md](../Skymap2D/README.md), [Text2Sound/README.md](../Text2Sound/README.md), [Rigging3D/README.md](../Rigging3D/README.md), [Animator3D/README.md](../Animator3D/README.md), [Paint3D/README.md](../Paint3D/README.md), [Materialize/README.md](../Materialize/README.md), [VibeGame/README.md](../VibeGame/README.md), [AiGameKitLab/README.md](../AiGameKitLab/README.md), [Terrain3D/README.md](../Terrain3D/README.md), [Rocks3D/README.md](../Rocks3D/README.md)
