"""Helper para ``<Project>/scripts/installer.py`` — delega ao Clified."""

from __future__ import annotations

import argparse
from pathlib import Path

from .monorepo import find_monorepo_root
from .unified import install_tool, list_available_tools


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--prefix", default=None, help="Prefixo (~/.local por defeito)")
    parser.add_argument("--python", default=None, help="Comando Python")
    parser.add_argument("--use-venv", action="store_true", help="(Legado) venv automático")
    parser.add_argument("--skip-deps", action="store_true", help="Saltar deps de sistema")
    parser.add_argument("--skip-models", action="store_true", help="Saltar passos de modelos")
    parser.add_argument("--force", action="store_true", help="Forçar reinstalação")
    parser.add_argument(
        "--action",
        choices=["install", "uninstall", "reinstall"],
        default="install",
        help="Acção (default: install)",
    )


def run_fixed_tool(tool_key: str, *, description: str) -> int:
    """Instala a ferramenta fixa ``tool_key`` (ex.: ``text2d``)."""
    parser = argparse.ArgumentParser(
        description=description,
        epilog=f"Forma oficial: ./install.sh {tool_key}",
    )
    _add_common_args(parser)
    args = parser.parse_args()
    monorepo = find_monorepo_root(Path(__file__).resolve())

    ok = install_tool(
        tool_key,
        monorepo=monorepo,
        action=args.action,
        install_prefix=Path(args.prefix) if args.prefix else None,
        python_cmd=args.python,
        use_venv=args.use_venv,
        skip_deps=args.skip_deps,
        skip_models=args.skip_models,
        force=args.force,
    )
    return 0 if ok else 1


def run_generic_tool_installer() -> int:
    """Instalador genérico com argumento ``tool`` (Animator3D/scripts/installer.py)."""
    parser = argparse.ArgumentParser(
        description="Instalador genérico — delega ao Clified (tools.yaml)",
    )
    parser.add_argument("tool", nargs="?", help="Ferramenta a instalar")
    parser.add_argument("--list", action="store_true", help="Listar ferramentas")
    _add_common_args(parser)
    args = parser.parse_args()
    monorepo = find_monorepo_root(Path(__file__).resolve())

    if args.list:
        print("Ferramentas disponíveis:")
        for spec in sorted(list_available_tools(monorepo), key=lambda s: s.cli_name):
            print(f"  - {spec.cli_name}  ({spec.description})")
        return 0

    if not args.tool:
        parser.print_help()
        return 1

    ok = install_tool(
        args.tool,
        monorepo=monorepo,
        action=args.action,
        install_prefix=Path(args.prefix) if args.prefix else None,
        python_cmd=args.python,
        use_venv=args.use_venv,
        skip_deps=args.skip_deps,
        skip_models=args.skip_models,
        force=args.force,
    )
    return 0 if ok else 1
