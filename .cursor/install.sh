#!/usr/bin/env bash
# Cloud Agent install script for the AiGameKit monorepo.
#
# Self-contained, idempotent, non-interactive bootstrap of the GPU-free
# development experience that CI validates (see .github/workflows/ci.yml):
#   - System libs + Python 3.13 (deadsnakes) + uv + Bun + a modern Rust stable
#   - Python 3.13 per-package venvs (Shared + the CI-tested packages) via uv/pip
#   - VibeGame (Bun) deps + engine build + hello-world example link
#   - Rust toolchain prefetch for Materialize + Viber (edition 2024)
#
# Every base-tool step is guarded by an existence check, so on a snapshot that
# already has the toolchains this script skips straight to the fast repo
# bootstrap. Heavy GPU-only stacks (Text2D/Text3D/Paint3D/Part3D/Terrain3D/
# Vramd/Motion3D/Intrinsic) are intentionally skipped: they need CUDA and are
# excluded from CI.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

export PATH="$HOME/.local/bin:$HOME/.bun/bin:$PATH"
export UV_LINK_MODE="${UV_LINK_MODE:-copy}"
export DEBIAN_FRONTEND=noninteractive

log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

# --- Base toolchain (idempotent) ------------------------------------------
# System libraries: Python 3.13 build/venv, plus the C/C++ + graphics/audio
# deps the Rust crates need (wgpu for Materialize; Bevy + vendored Luau/
# basis-universal C++ for Viber) and libmeshoptimizer for GLB compression.
if ! command -v python3.13 >/dev/null 2>&1 \
   || ! dpkg -s libasound2-dev >/dev/null 2>&1 \
   || ! dpkg -s libmeshoptimizer-dev >/dev/null 2>&1; then
  log "System packages + Python 3.13 (deadsnakes)"
  sudo add-apt-repository -y ppa:deadsnakes/ppa
  sudo apt-get update
  sudo apt-get install -y \
    python3.13 python3.13-venv python3.13-dev \
    build-essential pkg-config cmake clang g++ libssl-dev \
    libasound2-dev libudev-dev libwayland-dev libxkbcommon-dev \
    libx11-dev libxcursor-dev libxrandr-dev libxi-dev libmeshoptimizer-dev
fi

# The base image defaults cc/c++ to clang, which cannot find the libstdc++
# headers the vendored C++ deps need (Viber's basis-universal-sys + mlua/Luau
# fail with "'algorithm' file not found"). Point cc/c++ at gcc/g++ so cargo's
# cc-rs builds succeed for interactive builds too.
if command -v gcc >/dev/null 2>&1 && command -v g++ >/dev/null 2>&1; then
  sudo update-alternatives --install /usr/bin/cc cc /usr/bin/gcc 100 >/dev/null 2>&1 || true
  sudo update-alternatives --install /usr/bin/c++ c++ /usr/bin/g++ 100 >/dev/null 2>&1 || true
  sudo update-alternatives --set cc /usr/bin/gcc >/dev/null 2>&1 || true
  sudo update-alternatives --set c++ /usr/bin/g++ >/dev/null 2>&1 || true
fi

# uv (fast Python venv/installer).
if ! command -v uv >/dev/null 2>&1; then
  log "uv"
  curl -fsSL https://astral.sh/uv/install.sh | sh
fi

# Bun (VibeGame runtime + test/build).
if ! command -v bun >/dev/null 2>&1; then
  log "Bun"
  curl -fsSL https://bun.sh/install | bash
fi

# Rust >= 1.87 for edition 2024 (Materialize + Viber). The base image ships an
# older stable; bump it via rustup when present.
if command -v rustup >/dev/null 2>&1; then
  rustc_minor="$(rustc --version 2>/dev/null | sed -E 's/rustc 1\.([0-9]+).*/\1/')"
  if [ -z "${rustc_minor:-}" ] || [ "${rustc_minor:-0}" -lt 87 ]; then
    log "Rust stable (edition 2024)"
    rustup default stable
    rustup update stable
  fi
fi

# Pinned ruff (matches the CI lint job) + pre-commit, on PATH via uv tools.
if ! command -v ruff >/dev/null 2>&1; then
  uv tool install "ruff==0.15.8" >/dev/null 2>&1 || true
fi
command -v pre-commit >/dev/null 2>&1 || uv tool install pre-commit >/dev/null 2>&1 || true

# --- Toolchain sanity ------------------------------------------------------
command -v uv >/dev/null 2>&1 || { echo "uv missing on PATH"; exit 1; }
command -v python3.13 >/dev/null 2>&1 || { echo "python3.13 missing on PATH"; exit 1; }

# CI-tested Python packages. Shared first (foundation), then packages that
# install Shared[dev] by absolute path (mirrors CI's install step).
CI_PACKAGES=(GameAssets Texture2D Skymap2D Rigging3D Text2Sound AiGameKitLab Rocks3D Animator3D)

ensure_venv() {
  # Create the venv only when missing so re-runs stay fast and idempotent.
  # --seed installs pip: package pyproject files depend on Shared via a
  # relative URL (aigamekit-shared @ file:../Shared) that pip resolves from
  # the project dir; uv's wheel builder cannot resolve that relative path.
  [ -x .venv/bin/pip ] || { rm -rf .venv && uv venv --seed --python 3.13 .venv; }
}

setup_py_pkg() {
  local pkg="$1"
  log "Python venv: $pkg"
  ( cd "$ROOT/$pkg" \
      && ensure_venv \
      && .venv/bin/python -m pip install -q -e "$ROOT/Shared/.[dev]" \
      && .venv/bin/python -m pip install -q -e ".[dev]" )
}

log "Shared (foundation)"
( cd "$ROOT/Shared" \
    && ensure_venv \
    && .venv/bin/python -m pip install -q -e ".[dev]" )

for pkg in "${CI_PACKAGES[@]}"; do
  setup_py_pkg "$pkg"
done

# --- VibeGame (Bun) --------------------------------------------------------
if command -v bun >/dev/null 2>&1; then
  log "VibeGame (Bun) dependencies"
  ( cd "$ROOT/VibeGame" && bun install --frozen-lockfile )

  # Build the engine (dist/) so examples can resolve `aigamekit-vibegame`, and
  # link it into the hello-world example. The example imports the engine by
  # package name; the symlink + built dist make the documented
  # `bun install && bun run dev` work out of the box.
  log "VibeGame engine build + hello-world example link"
  ( cd "$ROOT/VibeGame" && bun run build )
  ( cd "$ROOT/VibeGame/examples/hello-world" \
      && bun install \
      && mkdir -p node_modules \
      && rm -rf node_modules/aigamekit-vibegame \
      && ln -s ../../.. node_modules/aigamekit-vibegame )
else
  echo "bun missing on PATH — skipping VibeGame"
fi

# --- Rust (Materialize + Viber) -------------------------------------------
if command -v cargo >/dev/null 2>&1; then
  log "Rust dependency prefetch (Materialize + Viber)"
  cargo fetch --manifest-path "$ROOT/Materialize/Cargo.toml" || true
  cargo fetch --manifest-path "$ROOT/Viber/Cargo.toml" || true
else
  echo "cargo missing on PATH — skipping Rust prefetch"
fi

log "Install complete."
