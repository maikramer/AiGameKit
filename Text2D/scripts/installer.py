#!/usr/bin/env python3
"""Instalador Text2D — delega ao clified-install."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _find_tools_yaml() -> Path:
    """Walk up to find tools.yaml (monorepo root)."""
    current = Path(__file__).resolve().parent
    for _ in range(10):
        candidate = current / "tools.yaml"
        if candidate.is_file():
            return candidate
        parent = current.parent
        if parent == current:
            break
        current = parent
    print("Erro: tools.yaml não encontrado.", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    tools_yaml = _find_tools_yaml()
    os.environ["CLIFIED_TOOLS"] = str(tools_yaml)
    sys.exit(subprocess.call([sys.executable, "-m", "clified.installer", "text2d", *sys.argv[1:]]))
