#!/usr/bin/env python3
"""
Instalador genérico — usa o instalador unificado do monorepo.

Este script é usado por módulos que não têm lógica de instalação customizada.
Para módulos com extras (Text3D, Part3D, Paint3D, etc.), use o install.sh oficial.
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

from gamedev_shared.installer.registry import list_available_tools  # noqa: E402
from gamedev_shared.installer.unified import install_tool  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Instalador genérico — usa o instalador unificado do monorepo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Forma oficial no monorepo: ./install.sh <tool>

Ferramentas disponíveis:
{", ".join(sorted(list_available_tools()))}

Exemplos:
  ./install.sh text2d
  python3 scripts/installer.py text2d
  python3 scripts/installer.py --list
""",
    )
    parser.add_argument("tool", nargs="?", help="Nome da ferramenta a instalar")
    parser.add_argument("--list", action="store_true", help="Listar ferramentas disponíveis")
    parser.add_argument("--prefix", default="/usr/local", help="Diretório de instalação")
    parser.add_argument("--python", default=None, help="Comando Python")
    parser.add_argument("--use-venv", action="store_true", help="Usar virtualenv")
    parser.add_argument("--skip-deps", action="store_true", help="Pular dependências")
    parser.add_argument("--skip-models", action="store_true", help="Pular modelos")
    parser.add_argument("--force", action="store_true", help="Forçar reinstalação")
    args = parser.parse_args()

    if args.list:
        print("Ferramentas disponíveis:")
        for tool in sorted(list_available_tools()):
            print(f"  - {tool}")
        return 0

    if not args.tool:
        parser.print_help()
        return 1

    try:
        success = install_tool(
            args.tool,
            monorepo=_monorepo_root,
            install_prefix=Path(args.prefix),
            python_cmd=args.python,
            use_venv=args.use_venv,
            skip_deps=args.skip_deps,
            skip_models=args.skip_models,
            force=args.force,
        )
        return 0 if success else 1
    except ValueError as e:
        print(f"Erro: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
