#!/usr/bin/env bash
# Bootstrap Clified (PyPI) — source a partir de install.sh dos projectos.
# Uso:
#   source "$(dirname "$0")/scripts/install-bootstrap.sh"
#   clified_bootstrap ai2print "$@"

clified_resolve_python() {
  if [[ -n "${PYTHON_CMD:-}" ]]; then
    if "${PYTHON_CMD}" -m pip --version &>/dev/null 2>&1; then
      printf '%s\n' "${PYTHON_CMD}"
      return 0
    fi
    echo "PYTHON_CMD=${PYTHON_CMD} não tem pip funcional." >&2
    return 1
  fi

  local c
  for c in python3.14 python3.13 python3.12 python3.11 python3.10 python3 /usr/bin/python3; do
    command -v "$c" &>/dev/null || continue
    "$c" -m pip --version &>/dev/null 2>&1 || continue
    printf '%s\n' "$c"
    return 0
  done

  echo "Nenhum Python com pip encontrado. Instale python3-full ou defina PYTHON_CMD." >&2
  return 1
}

clified_pip_install() {
  local py="$1" spec="$2"
  if "$py" -m pip install --user --upgrade "$spec"; then
    return 0
  fi
  echo "A repetir pip com --break-system-packages (PEP 668)…" >&2
  "$py" -m pip install --user --break-system-packages --upgrade "$spec"
}

clified_bootstrap() {
  local min_ver="${CLIFIED_MIN_VERSION:-0.4.1}"
  local py
  py="$(clified_resolve_python)" || return 1
  export PYTHON_CMD="$py"

  if command -v clified-install &>/dev/null; then
    exec clified-install "$@"
  fi
  if "$py" -c "import clified" 2>/dev/null; then
    exec "$py" -m clified "$@"
  fi

  echo "A instalar clified>=${min_ver} via pip (${py})…"
  clified_pip_install "$py" "clified>=${min_ver}" || return 1

  if command -v clified-install &>/dev/null; then
    exec clified-install "$@"
  fi

  local user_base user_bin
  user_base="$("$py" -m site --user-base 2>/dev/null || true)"
  if [[ -n "$user_base" ]]; then
    user_bin="${user_base}/bin/clified-install"
    if [[ -x "$user_bin" ]]; then
      export PATH="${user_base}/bin:${PATH}"
      exec clified-install "$@"
    fi
  fi

  exec "$py" -m clified "$@"
}
