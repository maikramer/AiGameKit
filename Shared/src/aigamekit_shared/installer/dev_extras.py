"""Instala o extra ``[dev]`` de cada pacote no seu próprio venv.

Cada pacote é testado a partir do venv dele (``make test-<tool>``), logo o pytest
tem de viver lá. Sem isto o Makefile cai em silêncio no interpretador do sistema
e a suite corre contra o que houver instalado à sorte — foi o que fez
``make test-motion3d`` falhar com ``ModuleNotFoundError: huggingface_hub``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import tomllib

from ..logging import Logger
from .base import has_uv, uv_cmd

DEV_EXTRA = "dev"


def read_dev_requirements(project_root: Path) -> list[str]:
    """Requirement specs in the ``[dev]`` extra, minus local/URL ones.

    Installing ``<root>[dev]`` would rebuild the package, and uv cannot resolve the
    ``aigamekit-shared @ file:../Shared`` pin without the absolutising step the
    Clified installer does for the main install. The extra only ever holds plain
    tooling (pytest, pytest-cov, ruff), so install those specs directly.
    """
    pyproject = Path(project_root) / "pyproject.toml"
    if not pyproject.is_file():
        return []
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return []
    extras = data.get("project", {}).get("optional-dependencies", {})
    specs = extras.get(DEV_EXTRA) or []
    return [str(spec) for spec in specs if "@" not in str(spec)]


def install_dev_extras_in_venv(
    *,
    venv_python: str | Path,
    project_root: str | Path,
    logger: Logger,
) -> bool:
    """Install the ``[dev]`` requirements into the venv behind ``venv_python``.

    Returns True when nothing had to be done (no ``[dev]`` extra) or the install
    succeeded. A failure here never blocks the tool install — the CLI still works,
    only the test suite would need a manual install.
    """
    root = Path(project_root).resolve()
    requirements = read_dev_requirements(root)
    if not requirements:
        return True

    python = str(venv_python)
    if has_uv():
        argv = [uv_cmd(), "pip", "install", "--python", python, *requirements]
    else:
        argv = [python, "-m", "pip", "install", *requirements]

    logger.info(f"Extras de teste ({DEV_EXTRA}) no venv...")
    proc = subprocess.run(argv, cwd=str(root), capture_output=True, text=True)
    if proc.returncode != 0:
        logger.warn(f"Extras {DEV_EXTRA} falharam: {(proc.stderr or proc.stdout or '').strip()[-400:]}")
        return False
    return True


def install_dev_extras_hook(installer: Any) -> bool:
    """``post_install`` adapter — reads venv/root/logger off the Clified installer."""
    return install_dev_extras_in_venv(
        venv_python=installer.venv_python,
        project_root=installer.project_root,
        logger=installer.logger,
    )
