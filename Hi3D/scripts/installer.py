#!/usr/bin/env python3
"""Instalador local — delega ao clified-install (forma oficial: ./install.sh hi3d)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

TOOL_KEY = Path(__file__).resolve().parents[1].name.lower()

LICENSE_NOTES = {
    "hi3d": (
        "NOTA DE LICENÇA: os pesos e a implementação de 2.º estágio estão sob a "
        "'Hi3D Open License Agreement 1.0'. Uso comercial é permitido, mas integrações "
        "comerciais têm de exibir 'Powered by Hi3D 1.0' (link para o site oficial) e, "
        "acima de 200k MAU ou 1M USD/mês, notificar o licenciador em 60 dias. "
        "Outputs standalone (o GLB refinado, sem materiais/derivados do modelo) não "
        "ficam sujeitos a branding. Ver Hi3D/README.md §Licença."
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
