#!/usr/bin/env bash
# Bootstrap Clified (PyPI) — wrapper fino sobre scripts/_bootstrap.sh.
# Sourced por install.sh dos projectos consumidores.
#
# Vendored do repo Clified (v0.9.0) com adições AiGameKit:
#   - clified_ensure_uv: instala `uv` (pip --user) quando ausente (não-fatal).
#   - CLIFIED_MIN_VERSION default 0.8.1 (features de tools.yaml do monorepo).
#
# Uso:
#   source "$(dirname "$0")/scripts/install-bootstrap.sh"
#   clified_bootstrap denv "$@"

# shellcheck source=_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/_bootstrap.sh"

# === AiGameKit: garantir uv (acelera a criação de venvs das tools) ===
# Não-fatal: se a instalação falhar, o clified usa venv clássico.
clified_ensure_uv() {
  local py="$1"
  if command -v uv >/dev/null 2>&1; then
    return 0
  fi
  if [[ "${CLIFIED_SKIP_UV:-0}" == "1" ]]; then
    echo "uv ausente — CLIFIED_SKIP_UV=1, a usar venv clássico." >&2
    return 0
  fi
  echo "uv não encontrado — a instalar via pip (${py})…" >&2
  clified_pip_install "$py" "uv" || true
  # Não-fatal: falha apenas deixa o install mais lento (venv clássico).
  return 0
}

clified_bootstrap() {
  local min_ver="${CLIFIED_MIN_VERSION:-0.8.1}"

  local py
  py="$(clified_resolve_python)" || return 1
  export PYTHON_CMD="$py"
  clified_prepend_user_scripts_to_path "$py"
  clified_ensure_uv "$py"

  if command -v clified-install >/dev/null 2>&1; then
    exec clified-install "$@"
  fi

  if "$py" -c "import clified" 2>/dev/null; then
    clified_prepend_user_scripts_to_path "$py"
    if command -v clified-install >/dev/null 2>&1; then
      exec clified-install "$@"
    fi
    exec "$py" -m clified "$@"
  fi

  echo "A instalar clified>=${min_ver} via pip (${py})…" >&2
  clified_pip_install "$py" "clified>=${min_ver}" || return 1
  clified_prepend_user_scripts_to_path "$py"
  if [[ "${CLIFIED_PERSIST_PATH:-1}" != "0" ]]; then
    clified_persist_user_scripts_to_path "$py"
  fi

  clified_exec "$py" "$@"
}
