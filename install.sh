#!/bin/bash
# Instalação GameDev via Clified (PyPI)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export CLIFIED_TOOLS="${CLIFIED_TOOLS:-$SCRIPT_DIR/tools.yaml}"
export UV_VENV_CLEAR="${UV_VENV_CLEAR:-1}"
export UV_LINK_MODE="${UV_LINK_MODE:-copy}"

# shellcheck source=scripts/install-bootstrap.sh
source "$SCRIPT_DIR/scripts/install-bootstrap.sh"
clified_bootstrap "$@"
