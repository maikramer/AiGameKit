#!/bin/bash
# =============================================================================
# GameDev Monorepo — Instalador Unificado (Linux/macOS)
# =============================================================================
#
# Instala qualquer ferramenta do monorepo GameDev.
#
# Uso:
#   ./install.sh <tool>           # Instalar uma ferramenta
#   ./install.sh all              # Instalar tudo
#   ./install.sh --all             # igual a «all»
#   ./install.sh --list           # Listar ferramentas
#   ./install.sh materialize      # Instalar Materialize (Rust)
#   ./install.sh vibegame         # VibeGame: Bun + Node; ~/.local/bin/vibegame
#   ./install.sh text2d              # Text2D/.venv + wrappers em ~/.local/bin
#   ./install.sh text3d              # instala Text2D primeiro, depois Text3D (text2d indispensável)
#   ./install.sh text3d --text2d-venv-only  # só text2d editável no venv Text3D (sem passo text2d dedicado)
#
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SHARED_ROOT="$SCRIPT_DIR/Shared"
SHARED_SRC="$SHARED_ROOT/src"
PYTHON_CMD="${PYTHON_CMD:-python3}"

RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
NC='\033[0m'

# -----------------------------------------------------------------------------
# 1) Preparar ambiente do instalador (antes de carregar gamedev_shared)
# -----------------------------------------------------------------------------
prepare_installer_environment() {
    echo -e "${CYAN}Preparando ambiente do instalador...${NC}"

    if [ ! -d "$SHARED_SRC/gamedev_shared" ]; then
        echo -e "${RED}✗ Monorepo incompleto: não existe $SHARED_SRC/gamedev_shared${NC}"
        echo "  Clona o repositório completo (pasta Shared/ é obrigatória)."
        exit 1
    fi

    if ! command -v "$PYTHON_CMD" &> /dev/null; then
        echo -e "${RED}✗ Python 3 não encontrado.${NC}"
        echo "Instale Python 3.13+:"
        echo "  Ubuntu/Debian: sudo apt install python3"
        echo "  macOS: brew install python3"
        exit 1
    fi

    if ! "$PYTHON_CMD" -c "import sys; assert sys.version_info >= (3, 13)" 2>/dev/null; then
        echo -e "${RED}✗ Python 3.13 ou superior é necessário.${NC}"
        "$PYTHON_CMD" -V 2>/dev/null || true
        exit 1
    fi

    # ── uv (Astral) — gestor rápido de pacotes e ambientes Python ──
    if ! command -v uv &> /dev/null; then
        echo -e "${CYAN}  → Instalando uv (gestor de pacotes rápido)...${NC}"
        if command -v curl &> /dev/null; then
            curl -LsSf https://astral.sh/uv/install.sh | sh 2>/dev/null
        elif command -v wget &> /dev/null; then
            wget -qO- https://astral.sh/uv/install.sh | sh 2>/dev/null
        else
            echo -e "${RED}✗ curl ou wget necessário para instalar uv.${NC}"
            echo "  Instale manualmente: https://docs.astral.sh/uv/getting-started/installation/"
        fi
        # Adiciona ao PATH da sessão actual
        if [ -f "$HOME/.local/bin/uv" ]; then
            export PATH="$HOME/.local/bin:$PATH"
            echo -e "${GREEN}  ✓ uv instalado: $(uv --version)${NC}"
        fi
    else
        echo -e "${GREEN}  ✓ uv disponível: $(uv --version)${NC}"
    fi

    export PYTHONPATH="$SHARED_SRC:${PYTHONPATH:-}"

    # Rich para o instalador — evita PEP 668 (distros «externally managed»): venv próprio sob Shared/.
    local INSTALLER_VENV="$SHARED_ROOT/.installer-venv"
    if "$PYTHON_CMD" -c "import rich" 2>/dev/null; then
        export GAME_DEV_INSTALLER_PYTHON="$PYTHON_CMD"
        return 0
    fi

    echo -e "${CYAN}  → Ambiente isolado do instalador (venv + Rich)...${NC}"
    local INSTALLER_PY="$INSTALLER_VENV/bin/python"

    if [ ! -x "$INSTALLER_PY" ]; then
        if command -v uv &> /dev/null; then
            if ! uv venv "$INSTALLER_VENV" --seed --python "$PYTHON_CMD" --clear; then
                echo -e "${RED}✗ Falha ao criar $INSTALLER_VENV com uv.${NC}"
                exit 1
            fi
        else
            if ! "$PYTHON_CMD" -m venv "$INSTALLER_VENV"; then
                echo -e "${RED}✗ Falha ao criar venv do instalador.${NC}"
                echo "  Em Debian/Ubuntu: sudo apt install python3-venv"
                exit 1
            fi
        fi
    fi

    if ! "$INSTALLER_PY" -c "import rich" 2>/dev/null; then
        if ! "$INSTALLER_PY" -m pip install -q --upgrade pip "rich>=13"; then
            echo -e "${RED}✗ Falha ao instalar Rich no venv do instalador.${NC}"
            exit 1
        fi
    fi

    export GAME_DEV_INSTALLER_PYTHON="$INSTALLER_PY"
}

prepare_installer_environment

# ``uv`` (venv/pip): sem prompts sobre .venv existente; sem warnings de hardlink cross-filesystem.
export UV_VENV_CLEAR=1
export UV_LINK_MODE=copy

echo -e "${CYAN}GameDev Monorepo — Instalador Unificado${NC}"
echo "========================================"

exec "${GAME_DEV_INSTALLER_PYTHON:?}" -m gamedev_shared.installer.unified "$@"
