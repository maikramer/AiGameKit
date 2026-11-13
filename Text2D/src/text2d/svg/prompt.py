"""Prompt enhancement determinístico para saída vectorial.

O FLUX.3 foi treinado em arte raster; pedir ``painterly``/gradientes produz uma
imagem com milhares de cores, que o VTracer transforma num SVG enorme e o
StarVector (treinado em ícones/logos) não consegue interpretar. Este módulo
troca os termos raster por directivas *vector-friendly* e acrescenta o hint
do estilo. É idempotente e não toca no sujeito nem no bloco ``Avoid:``.
"""

from __future__ import annotations

import re

from .options import STYLE_HINTS, STYLE_MARKERS

# Termos que empurram a imagem para raster/pintura. A ordem importa: as
# negações ("no gradients") são removidas antes das substituições, senão
# "no gradients" virava "no solid flat colors".
_NEGATED_TERMS = (
    r"\bno\s+gradients?\b",
    r"\bwithout\s+gradients?\b",
    r"\bno\s+textures?\b",
    r"\bwithout\s+textures?\b",
    r"\bno\s+shadows?\b",
    r"\bwithout\s+shadows?\b",
    r"\bno\s+grain\b",
    r"\bno\s+noise\b",
)

_RASTER_TERMS: tuple[tuple[str, str], ...] = (
    (r"\b(?:hand[- ]painted|painterly|visible\s+brush\s+strokes?|brush\s+strokes?)\b", "flat vector shapes"),
    (r"\bphotorealistic\b", "stylized vector illustration"),
    (r"\brealistic\b", "stylized"),
    (r"\b3d\s+render(?:ed|ing)?\b", "2D vector illustration"),
    (r"\bcgi\b", "2D vector illustration"),
    (r"\boctane(?:\s+render)?\b", "2D vector illustration"),
    (r"\bgradients?\b", "solid flat colors"),
    (r"\btextures?\b", "clean flat areas"),
    (r"\bgrain\b", "clean flat areas"),
    (r"\bnoise\b", "clean flat areas"),
    (r"\bsoft\s+shadows?\b", "no shadows"),
    (r"\bambient\s+occlusion\b", "no shadows"),
    (r"\bdepth\s+of\s+field\b", "sharp focus"),
    (r"\bbokeh\b", "sharp focus"),
)


def _cleanup_commas(text: str) -> str:
    text = re.sub(r"\s*,\s*(?:,\s*)+", ", ", text)
    text = re.sub(r"^\s*[,;]\s*", "", text)
    text = re.sub(r"\s*[,;]\s*$", "", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def _strip_raster_terms(text: str) -> str:
    for pattern in _NEGATED_TERMS:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)
    for pattern, replacement in _RASTER_TERMS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return _cleanup_commas(text)


def enhance_prompt_for_svg(
    prompt: str,
    *,
    style: str = "flat",
    colors: int = 8,
) -> str:
    """Devolve o prompt com directivas vector-friendly.

    Args:
        prompt: Prompt original (pode ser o prompt já augmentado por
            ``--category icon`` / ``--pose``).
        style: ``flat``, ``line`` ou ``detailed`` (ver ``SvgOptions``).
        colors: tamanho da paleta pedida ao raster.

    Returns:
        O prompt inalterado se já contiver o marcador do estilo; caso
        contrário, o prompt com os termos raster substituídos e o hint do
        estilo acrescentado antes do bloco ``Avoid:``.
    """

    text = (prompt or "").strip()
    if not text:
        return text

    hint = STYLE_HINTS.get(style)
    marker = STYLE_MARKERS.get(style)
    if hint is None or marker is None:
        raise ValueError(f"estilo SVG inválido: {style!r}")

    head, sep, tail = text.partition("Avoid:")
    if marker.lower() in head.lower():
        return text

    head = _strip_raster_terms(head)
    if not head:
        head = "subject"
    hint_text = hint.format(colors=int(colors))

    result = f"{head}, {hint_text}"
    if sep:
        result = f"{result}. Avoid:{tail}"
    return _cleanup_commas(result)
