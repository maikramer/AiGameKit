#!/usr/bin/env python3
"""Instalador Intrinsic — delega ao clified-install (padrão do monorepo)."""

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
    print(
        "NOTA DE LICENÇA: o modelo upstream (compphoto/Intrinsic) é "
        "'academic use only' + patente pendente. Uso comercial requer "
        "licença dos autores (ver Intrinsic/README.md).",
        file=sys.stderr,
    )
    tools_yaml = _find_tools_yaml()
    os.environ["CLIFIED_TOOLS"] = str(tools_yaml)
    sys.exit(subprocess.call([sys.executable, "-m", "clified.installer", "intrinsic", *sys.argv[1:]]))
