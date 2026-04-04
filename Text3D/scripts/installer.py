#!/usr/bin/env python3
"""
Instalador Text3D — usa o instalador unificado do monorepo.

Uso:
  ./install.sh text3d
  python3 scripts/installer.py
  gamedev-install text3d
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Setup path para gamedev_shared
_script_dir = Path(__file__).resolve().parent
_project_root = _script_dir.parent
_monorepo_root = _project_root.parent
_shared_src = _monorepo_root / "Shared" / "src"
if _shared_src.is_dir() and str(_shared_src) not in sys.path:
    sys.path.insert(0, str(_shared_src))

from gamedev_shared.installer.unified import install_tool


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Instalador Text3D",
        epilog="""
Forma oficial: ./install.sh text3d

Nota: Text3D instala Text2D automaticamente (indispensável para text-to-3D).

Exemplos:
  ./install.sh text3d
  python3 scripts/installer.py --force
  python3 scripts/installer.py --text2d-venv-only  # só Text2D no venv do Text3D
""",
    )
    parser.add_argument("--prefix", default=str(Path.home() / ".local"), help="Diretório de instalação")
    parser.add_argument("--python", default=None, help="Comando Python")
    parser.add_argument("--use-venv", action="store_true", help="Usar virtualenv")
    parser.add_argument("--skip-deps", action="store_true", help="Pular dependências")
    parser.add_argument("--skip-models", action="store_true", help="Pular modelos")
    parser.add_argument("--skip-env-config", action="store_true", help="Pular configuração de ambiente")
    parser.add_argument("--text2d-venv-only", action="store_true", help="Instalar Text2D só no venv do Text3D")
    parser.add_argument("--force", action="store_true", help="Forçar reinstalação")
    args = parser.parse_args()

    success = install_tool(
        "text3d",
        monorepo=_monorepo_root,
        install_prefix=Path(args.prefix),
        python_cmd=args.python,
        use_venv=args.use_venv,
        skip_deps=args.skip_deps,
        skip_models=args.skip_models,
        force=args.force,
        skip_env_config=args.skip_env_config,
        text2d_venv_only=args.text2d_venv_only,
    )
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
