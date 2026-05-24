"""Ponte para o instalador Clified (``tools.yaml`` na raiz do monorepo)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from .base import default_python_command
from .monorepo import find_monorepo_root

if TYPE_CHECKING:
    from clified.installer.registry import ToolSpec


def _clified_root() -> Path:
    env = os.environ.get("CLIFIED_ROOT", "").strip()
    if env:
        return Path(env).resolve()
    return Path.home() / "AI" / "clified"


def ensure_clified_env(monorepo: Path | None = None) -> Path:
    """Define ``CLIFIED_TOOLS`` e variáveis auxiliares; devolve a raiz do monorepo."""
    root = monorepo or find_monorepo_root()
    os.environ.setdefault("CLIFIED_ROOT", str(_clified_root()))
    os.environ["CLIFIED_TOOLS"] = str(root / "tools.yaml")
    os.environ.setdefault("UV_VENV_CLEAR", "1")
    os.environ.setdefault("UV_LINK_MODE", "copy")
    return root


def _clified_python() -> str:
    clified = _clified_root()
    if sys.platform == "win32":
        candidate = clified / ".installer-venv" / "Scripts" / "python.exe"
    else:
        candidate = clified / ".installer-venv" / "bin" / "python"
    if candidate.is_file():
        return str(candidate)
    return os.environ.get("PYTHON_CMD", "").strip() or sys.executable


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
    """Instala uma ferramenta registada em ``tools.yaml`` via Clified."""
    ensure_clified_env(monorepo)
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
    from clified.installer.registry import (
        get_workspace,
        list_available_tools as _list,
        load_registry,
    )

    load_registry()
    return _list(get_workspace().root)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    monorepo = ensure_clified_env()
    clified = _clified_root()
    install_sh = clified / "install.sh"

    if sys.platform != "win32" and install_sh.is_file():
        env = os.environ.copy()
        cmd = [str(install_sh), *args]
        return subprocess.call(cmd, cwd=monorepo, env=env)

    env = os.environ.copy()
    py = _clified_python()
    return subprocess.call([py, "-m", "clified", *args], cwd=monorepo, env=env)


if __name__ == "__main__":
    sys.exit(main())
