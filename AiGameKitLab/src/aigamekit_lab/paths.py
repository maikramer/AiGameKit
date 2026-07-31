"""Raiz do repositório AiGameKit (monorepo) para caminhos relativos."""

from __future__ import annotations

import os
from pathlib import Path


def aigamekit_repo_root() -> Path:
    """Diretório raiz do monorepo (pasta que contém AiGameKitLab, Text3D, …)."""
    env = os.environ.get("AIGAMEKIT_ROOT", "").strip()
    if env:
        return Path(env).resolve()
    # AiGameKitLab/src/aigamekit_lab/paths.py → parents[3] == AiGameKit
    here = Path(__file__).resolve()
    return here.parent.parent.parent.parent
