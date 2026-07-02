#!/usr/bin/env bash
# Bootstrap Clified (PyPI) — wrapper fino sobre scripts/_bootstrap.sh.
# Sourced por install.sh dos projectos consumidores.
#
# Uso:
#   source "$(dirname "$0")/scripts/install-bootstrap.sh"
#   clified_bootstrap denv "$@"

# shellcheck source=_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/_bootstrap.sh"

clified_bootstrap() {
  local min_ver="${CLIFIED_MIN_VERSION:-0.7.3}"

  local py
  py="$(clified_resolve_python)" || return 1
  export PYTHON_CMD="$py"
  clified_prepend_user_scripts_to_path "$py"

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
