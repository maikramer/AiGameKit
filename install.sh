#!/bin/bash
# Instalação AiGameKit via Clified (PyPI)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export CLIFIED_TOOLS="${CLIFIED_TOOLS:-$SCRIPT_DIR/tools.yaml}"
export UV_VENV_CLEAR="${UV_VENV_CLEAR:-1}"
export UV_LINK_MODE="${UV_LINK_MODE:-copy}"

# shellcheck source=scripts/install-bootstrap.sh
source "$SCRIPT_DIR/scripts/install-bootstrap.sh"

# Perfis de instalação (docs/findings/TOOLKIT_CORE_PROFILE_STUDY.md):
#   ./install.sh core      — zero-a-jogo mínimo: DAG GLB animado → browser
#   ./install.sh examples  — tudo que os jogos de exemplo usam (core + céu/áudio/
#                            texturas/terreno/rochas + track nativa Viber)
#   ./install.sh --all     — catálogo completo (acrescenta part3d, motion3d, intrinsic)
CORE_TOOLS=(vramd text2d text3d paint3d rigging3d animator3d gameassets materialize aigamekitlab vibegame)
EXAMPLES_EXTRA_TOOLS=(texture2d skymap2d text2sound terrain3d rocks3d viber)

case "${1:-}" in
  core|examples)
    profile="$1"
    shift
    tools=("${CORE_TOOLS[@]}")
    if [ "$profile" = "examples" ]; then
      tools+=("${EXAMPLES_EXTRA_TOOLS[@]}")
    fi
    echo "Perfil '$profile': ${tools[*]}"
    for t in "${tools[@]}"; do
      ( clified_bootstrap "$t" "$@" )
    done
    ;;
  *)
    clified_bootstrap "$@"
    ;;
esac
