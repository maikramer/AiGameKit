#!/usr/bin/env python3
"""
Instalador TerrainGen — usa o instalador unificado do monorepo.

Uso:
  ./install.sh terraingen
  python3 scripts/installer.py
  gamedev-install terraingen
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
        description="Instalador TerrainGen",
        epilog="""
Forma oficial: ./install.sh terraingen

Exemplos:
  ./install.sh terraingen
  python3 scripts/installer.py --force
""",
    )
    parser.add_argument("--prefix", default="/usr/local", help="Diretório de instalação")
    parser.add_argument("--python", default=None, help="Comando Python")
    parser.add_argument("--use-venv", action="store_true", help="Usar virtualenv")
    parser.add_argument("--skip-deps", action="store_true", help="Pular dependências")
    parser.add_argument("--skip-models", action="store_true", help="Pular modelos")
    parser.add_argument("--force", action="store_true", help="Forçar reinstalação")
    args = parser.parse_args()

    success = install_tool(
        "terraingen",
        monorepo=_monorepo_root,
        install_prefix=Path(args.prefix),
        python_cmd=args.python,
        use_venv=args.use_venv,
        skip_deps=args.skip_deps,
        skip_models=args.skip_models,
        force=args.force,
    )
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
