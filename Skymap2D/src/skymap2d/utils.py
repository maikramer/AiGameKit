"""Funções utilitárias para Skymap2D — shims sobre ``aigamekit_shared``.

As validações vivem em ``aigamekit_shared.validation``; aqui ficam os defaults
do Skymap2D (max_width 4096, ratio 2:1 com warning, guidance 6.0, steps 40,
2048x1024) e os re-exports históricos.
"""

from __future__ import annotations

from typing import Any

from aigamekit_shared.gpu import format_bytes  # noqa: F401
from aigamekit_shared.logging import Logger as _Logger
from aigamekit_shared.path_utils import ensure_directory  # noqa: F401
from aigamekit_shared.seed_utils import generate_seed  # noqa: F401
from aigamekit_shared.validation import (
    format_timestamp,  # noqa: F401
    validate_prompt,  # noqa: F401
)
from aigamekit_shared.validation import (
    validate_dimensions as _validate_dimensions,
)
from aigamekit_shared.validation import (
    validate_params as _validate_params,
)

_logger = _Logger()


def validate_dimensions(width: int, height: int) -> tuple[bool, str | None]:
    """Valida dimensões de imagem para skymap equirectangular.

    Recomenda ratio 2:1 com tolerância de 5%.

    Returns:
        Tuple (is_valid, error_message).
    """
    return _validate_dimensions(
        width,
        height,
        max_width=4096,
        max_height=2048,
        warn_ratio=2.0,
        logger=_logger,
    )


def validate_params(params: dict[str, Any]) -> tuple[bool, str | None]:
    """Valida parâmetros de geração de skymap.

    Returns:
        Tuple (is_valid, error_message).
    """
    return _validate_params(
        params,
        default_guidance=6.0,
        default_steps=40,
        default_width=2048,
        default_height=1024,
        max_width=4096,
        warn_ratio=2.0,
        logger=_logger,
    )
