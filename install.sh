#!/bin/bash
# Instalação GameDev via Clified (sem lógica hardcoded — tools.yaml no repo)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLIFIED_ROOT="${CLIFIED_ROOT:-$HOME/AI/clified}"

if [[ ! -x "$CLIFIED_ROOT/install.sh" ]]; then
  echo "Clified não encontrado em: $CLIFIED_ROOT" >&2
  echo "Clone https://github.com/maikramer/clified ou defina CLIFIED_ROOT." >&2
  exit 1
fi

export CLIFIED_ROOT
export CLIFIED_TOOLS="${CLIFIED_TOOLS:-$SCRIPT_DIR/tools.yaml}"
export UV_VENV_CLEAR="${UV_VENV_CLEAR:-1}"
export UV_LINK_MODE="${UV_LINK_MODE:-copy}"

exec "$CLIFIED_ROOT/install.sh" "$@"
