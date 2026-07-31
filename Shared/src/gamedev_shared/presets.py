"""Accessors de presets partilhados sobre dicts de conteúdo por-tool.

Texture2D/Skymap2D tinham os mesmos 4 accessors (``get_preset``,
``list_presets``, ``get_preset_prompt``, ``get_preset_params``) byte-a-byte;
Text2Sound tinha variantes (lista ordenada, lookup case-insensitive com
KeyError). O conteúdo (os dicts de presets em si) continua em cada tool — este
módulo só padroniza o acesso.

Cada tool mantém ``presets.py`` com o seu dict + shims de 1 linha por accessor
(para não partir imports de CLI/generator/tests).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def get_preset(presets: Mapping[str, dict[str, Any]], name: str) -> dict[str, Any] | None:
    """Obtém um preset pelo nome (``None`` se não existir)."""
    return presets.get(name)


def list_presets(presets: Mapping[str, Any], *, sorted_names: bool = False) -> list[str]:
    """Lista os nomes dos presets, na ordem do dict ou ordenados."""
    names = list(presets.keys())
    return sorted(names) if sorted_names else names


def get_preset_prompt(
    presets: Mapping[str, dict[str, Any]],
    name: str,
    *,
    key: str = "prompt",
) -> str | None:
    """Obtém o prompt (ou outra chave) de um preset."""
    preset = get_preset(presets, name)
    return preset.get(key) if preset else None


def get_preset_params(
    presets: Mapping[str, dict[str, Any]],
    name: str,
    *,
    exclude_prompt: bool = True,
) -> dict[str, Any] | None:
    """Obtém os parâmetros de geração de um preset.

    Args:
        presets: Dict de presets da tool.
        name: Nome do preset.
        exclude_prompt: Remove a chave ``prompt`` do resultado (comportamento
            das tools Texture2D/Skymap2D — o prompt usa-se via
            :func:`get_preset_prompt`).
    """
    preset = get_preset(presets, name)
    if not preset:
        return None
    params = dict(preset)
    if exclude_prompt:
        params.pop("prompt", None)
    return params


def get_preset_ci(presets: Mapping[str, dict[str, Any]], name: str) -> dict[str, Any]:
    """Obtém um preset por nome case-insensitive (separadores normalizados).

    Raises:
        KeyError: Preset não encontrado.
    """
    key = name.lower().replace(" ", "-").replace("_", "-")
    if key in presets:
        return presets[key]
    raise KeyError(f"Preset desconhecido: {name!r}. Disponíveis: {', '.join(sorted(presets))}")
