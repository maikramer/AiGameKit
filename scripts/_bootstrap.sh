#!/usr/bin/env bash
# =============================================================================
# Vendored do repo Clified (v0.9.0) — manter em sincronia com upstream.
# Primitivas partilhadas de bootstrap do Clified (Python + pip install + exec).
# Sourced por scripts/install-bootstrap.sh e por install.sh de projectos
# consumidores. NÃO chama exit por si — a orquestração fica no wrapper.
# =============================================================================

clified_resolve_python() {
  if [[ -n "${PYTHON_CMD:-}" ]]; then
    if "${PYTHON_CMD}" -m pip --version >/dev/null 2>&1; then
      printf '%s\n' "${PYTHON_CMD}"
      return 0
    fi
    echo "PYTHON_CMD=${PYTHON_CMD} não tem pip funcional." >&2
    return 1
  fi

  local c
  for c in python3.14 python3.13 python3.12 python3.11 python3.10 python3 /usr/bin/python3; do
    command -v "$c" >/dev/null 2>&1 || continue
    "$c" -m pip --version >/dev/null 2>&1 || continue
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

clified_user_script_dirs() {
  local py="$1"
  local user_base
  user_base="$("$py" -m site --user-base 2>/dev/null || true)"
  if [[ -n "$user_base" ]]; then
    printf '%s\n' "${user_base}/bin"
  fi
}

clified_path_contains_dir() {
  local dir="$1"
  [[ -d "$dir" ]] || return 1
  local part
  IFS=':' read -r -a _parts <<< "${PATH:-}"
  for part in "${_parts[@]}"; do
    [[ -z "$part" ]] && continue
    if [[ "$(cd "$part" 2>/dev/null && pwd -P)" == "$(cd "$dir" 2>/dev/null && pwd -P)" ]]; then
      return 0
    fi
  done
  return 1
}

clified_prepend_user_scripts_to_path() {
  local py="$1"
  local dir
  while IFS= read -r dir; do
    [[ -d "$dir" ]] || continue
    if ! clified_path_contains_dir "$dir"; then
      export PATH="${dir}:${PATH}"
    fi
  done < <(clified_user_script_dirs "$py")
}

clified_persist_user_scripts_to_path() {
  local py="$1"
  local dir marker="# clified: pip --user scripts on PATH"
  local rc added=0

  while IFS= read -r dir; do
    [[ -d "$dir" ]] || continue
    for rc in "${HOME}/.profile" "${HOME}/.bash_profile" "${HOME}/.zprofile"; do
      if [[ -f "$rc" ]] && grep -qF "$marker" "$rc" 2>/dev/null; then
        if ! grep -qF "$dir" "$rc" 2>/dev/null; then
          {
            echo ""
            echo "$marker"
            echo "export PATH=\"${dir}:\${PATH}\""
          } >>"$rc"
          added=1
        fi
        break
      fi
    done
    if [[ "$added" -eq 0 ]]; then
      rc="${HOME}/.profile"
      if [[ ! -f "$rc" ]]; then
        touch "$rc"
      fi
      if ! grep -qF "$dir" "$rc" 2>/dev/null; then
        {
          echo ""
          echo "$marker"
          echo "export PATH=\"${dir}:\${PATH}\""
        } >>"$rc"
        added=1
      fi
    fi
  done < <(clified_user_script_dirs "$py")
}

clified_find_user_bin() {
  local py="$1"
  local user_base
  user_base="$("$py" -m site --user-base 2>/dev/null || true)"
  if [[ -n "$user_base" && -x "${user_base}/bin/clified-install" ]]; then
    printf '%s\n' "${user_base}/bin/clified-install"
  fi
}

clified_exec() {
  local py="$1"
  shift
  clified_prepend_user_scripts_to_path "$py"
  if command -v clified-install >/dev/null 2>&1; then
    exec clified-install "$@"
  fi
  exec "$py" -m clified "$@"
}

clified_ensure_engine() {
  local py spec persist="${CLIFIED_PERSIST_PATH:-1}"
  py="$(clified_resolve_python)" || return 1
  if ! "$py" -c "import sys; assert sys.version_info >= (3, 10)" 2>/dev/null; then
    echo "Python 3.10+ necessário." >&2
    return 1
  fi
  export PYTHON_CMD="$py"
  clified_prepend_user_scripts_to_path "$py"

  if command -v clified-install >/dev/null 2>&1; then
    return 0
  fi
  if "$py" -c "import clified" 2>/dev/null; then
    clified_prepend_user_scripts_to_path "$py"
    command -v clified-install >/dev/null 2>&1 && return 0
    return 0
  fi

  spec="${CLIFIED_VERSION:-clified}"
  echo "A instalar o motor clified via pip (${py})…" >&2
  clified_pip_install "$py" "$spec" || return 1
  clified_prepend_user_scripts_to_path "$py"
  if [[ "$persist" != "0" ]]; then
    clified_persist_user_scripts_to_path "$py"
  fi
  return 0
}
