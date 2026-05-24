#!/bin/bash
# Instalação GameDev via Clified (PyPI)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export CLIFIED_TOOLS="${CLIFIED_TOOLS:-$SCRIPT_DIR/tools.yaml}"
export UV_VENV_CLEAR="${UV_VENV_CLEAR:-1}"
export UV_LINK_MODE="${UV_LINK_MODE:-copy}"

PYTHON_CMD="${PYTHON_CMD:-python3}"
MIN_VERSION="${CLIFIED_MIN_VERSION:-0.4.0}"

if command -v clified-install &>/dev/null; then
  exec clified-install "$@"
fi
if "$PYTHON_CMD" -c "import clified" 2>/dev/null; then
  exec "$PYTHON_CMD" -m clified "$@"
fi

echo "A instalar clified>=${MIN_VERSION} via pip..."
"$PYTHON_CMD" -m pip install --user --upgrade "clified>=${MIN_VERSION}"
exec clified-install "$@"
