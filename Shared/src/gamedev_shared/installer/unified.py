"""Ponte para o instalador Clified (``tools.yaml`` na raiz do monorepo)."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from .base import default_python_command
from .monorepo import find_monorepo_root

if TYPE_CHECKING:
    from clified.installer.registry import ToolSpec


def ensure_clified_env(monorepo: Path | None = None) -> Path:
    """Define ``CLIFIED_TOOLS``; devolve a raiz do monorepo."""
    root = monorepo or find_monorepo_root()
    os.environ["CLIFIED_TOOLS"] = str(root / "tools.yaml")
    os.environ.setdefault("UV_VENV_CLEAR", "1")
    os.environ.setdefault("UV_LINK_MODE", "copy")
    return root


def _ensure_clified_importable() -> None:
    try:
        import clified  # noqa: F401
    except ImportError:
        import subprocess

        min_ver = os.environ.get("CLIFIED_MIN_VERSION", "0.4.0")
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--user",
                f"clified>={min_ver}",
            ],
            check=True,
        )


def install_tool(
    name: str,
    *,
    monorepo: Path | None = None,
    action: str = "install",
    install_prefix: Path | None = None,
    python_cmd: str | None = None,
    use_venv: bool = False,
    skip_deps: bool = False,
    skip_models: bool = False,
    force: bool = False,
    text2d_venv_only: bool = False,
) -> bool:
    ensure_clified_env(monorepo)
    _ensure_clified_importable()
    from clified.installer.registry import load_registry
    from clified.installer.unified import install_tool as _clified_install

    load_registry()
    return _clified_install(
        name,
        action=action,
        install_prefix=install_prefix,
        python_cmd=python_cmd or default_python_command(),
        use_venv=use_venv,
        skip_deps=skip_deps,
        skip_models=skip_models,
        force=force,
        text2d_venv_only=text2d_venv_only,
    )


def install_all(
    *,
    monorepo: Path | None = None,
    install_prefix: Path | None = None,
    python_cmd: str | None = None,
    use_venv: bool = False,
    skip_deps: bool = False,
    skip_models: bool = False,
    force: bool = False,
) -> bool:
    ensure_clified_env(monorepo)
    _ensure_clified_importable()
    from clified.installer.registry import load_registry
    from clified.installer.unified import install_all as _clified_install_all

    load_registry()
    return _clified_install_all(
        install_prefix=install_prefix,
        python_cmd=python_cmd or default_python_command(),
        use_venv=use_venv,
        skip_deps=skip_deps,
        skip_models=skip_models,
        force=force,
    )


def list_available_tools(monorepo: Path | None = None) -> list[ToolSpec]:
    ensure_clified_env(monorepo)
    _ensure_clified_importable()
    from clified.installer.registry import (
        get_workspace,
        list_available_tools as _list,
        load_registry,
    )

    load_registry()
    return _list(get_workspace().root)


def main(argv: list[str] | None = None) -> int:
    from clified.installer.bootstrap import run

    args = list(sys.argv[1:] if argv is None else argv)
    monorepo = ensure_clified_env()
    return run(args, cwd=monorepo)


if __name__ == "__main__":
    sys.exit(main())
