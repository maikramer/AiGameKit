"""Styling de ícones para o Text2D (porta do Text2Icon, substituído pelo FLUX Klein).

A geração de ícones passou a ser uma categoria do Text2D: ``--category icon``
resolve 512² + 2 passos via QualityEngine e augmenta o prompt com instruções
de app-icon. Este módulo é a porta direta do ``text2icon.generator`` (mesma
string de instruções e mesma lógica de idempotência).
"""

from __future__ import annotations

import re

# Nome da categoria de asset no QualityEngine (asset-categories.yaml).
ICON_CATEGORY = "icon"

BASE_ICON_INSTRUCTIONS = (
    "app icon, simple, centered, bold, clean background, high contrast, "
    "flat design, crisp edges, single subject, readable at small size"
)

# Marcadores que indicam prompt já "icon-aware" — sem duplicar instruções.
_ICON_MARKER_RE = re.compile(r"\b(icon|app icon|logo|emblem|badge|glyph)\b", re.IGNORECASE)


def augment_prompt_for_icon(prompt: str) -> str:
    """Acrescenta instruções de ícone app-icon automaticamente.

    Se o utilizador já menciona "icon" / "app icon" / "logo", não duplica.

    Args:
        prompt: Prompt original do utilizador.

    Returns:
        Prompt com as instruções de ícone prefixadas (ou inalterado quando
        o prompt já contém um marcador de ícone).
    """
    p = (prompt or "").strip()
    if not p:
        return p
    if _ICON_MARKER_RE.search(p):
        return p
    return f"{BASE_ICON_INSTRUCTIONS}, {p}"
