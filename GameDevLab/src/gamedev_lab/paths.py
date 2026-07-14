"""Raiz do repositório GameDev (monorepo) para caminhos relativos."""

from __future__ import annotations

import os
from pathlib import Path


def gamedev_repo_root() -> Path:
    """Diretório raiz do monorepo (pasta que contém GameDevLab, Text3D, …)."""
    env = os.environ.get("GAMEDEV_ROOT", "").strip()
    if env:
        return Path(env).resolve()
    # GameDevLab/src/gamedev_lab/paths.py → parents[3] == GameDev
    here = Path(__file__).resolve()
    return here.parent.parent.parent.parent
