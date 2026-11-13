"""text2svg dentro do Text2D: prompt enhancement + VTracer/StarVector.

A API pesada (torch/vtracer) é importada de forma preguiçosa para o
``text2d --help`` continuar leve.
"""

from __future__ import annotations

from .options import (
    SVG_DETAILS,
    SVG_ENGINES,
    SVG_STYLES,
    SvgEngineUnavailable,
    SvgOptions,
    SvgResult,
    resolve_engine,
)
from .prompt import enhance_prompt_for_svg

__all__ = [
    "SVG_DETAILS",
    "SVG_ENGINES",
    "SVG_STYLES",
    "SvgEngineUnavailable",
    "SvgOptions",
    "SvgResult",
    "enhance_prompt_for_svg",
    "resolve_engine",
    "vectorize_file",
]


def vectorize_file(*args, **kwargs):
    """Wrapper preguiçoso de :func:`text2d.svg.api.vectorize_file`."""

    from .api import vectorize_file as _impl

    return _impl(*args, **kwargs)
