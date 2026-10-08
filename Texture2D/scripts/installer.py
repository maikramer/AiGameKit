#!/usr/bin/env python3
"""Instalador local — delega ao clified-install (forma oficial: ./install.sh <tool> na raiz)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# A chave da tool no tools.yaml é o nome da pasta em minúsculas
# (Text3D → text3d, AiGameKitLab → aigamekitlab, ...).
TOOL_KEY = Path(__file__).resolve().parents[1].name.lower()

LICENSE_NOTES = {
    "intrinsic": (
        "NOTA DE LICENÇA: o modelo upstream (compphoto/Intrinsic) é "
        "'academic use only' + patente pendente. Uso comercial requer "
        "licença dos autores (ver Intrinsic/README.md)."
    ),
}


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
    note = LICENSE_NOTES.get(TOOL_KEY)
    if note:
        print(note, file=sys.stderr)
    tools_yaml = _find_tools_yaml()
    os.environ["CLIFIED_TOOLS"] = str(tools_yaml)
    sys.exit(subprocess.call([sys.executable, "-m", "clified.installer", TOOL_KEY, *sys.argv[1:]]))
