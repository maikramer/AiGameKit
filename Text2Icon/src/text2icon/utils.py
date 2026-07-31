"""Funções utilitárias para Text2Icon — shims sobre ``gamedev_shared``.

As validações vivem em ``gamedev_shared.validation`` (parâmetros por-tool);
aqui ficam os defaults do Text2Icon (Sana Sprint: max_length 1000, 1-4 steps,
guidance 4.5, resolução 512²) e os re-exports históricos.
"""

from __future__ import annotations

from typing import Any

from gamedev_shared.gpu import format_bytes  # noqa: F401
from gamedev_shared.path_utils import ensure_directory  # noqa: F401
from gamedev_shared.seed_utils import generate_seed  # noqa: F401
from gamedev_shared.validation import (
    format_timestamp,  # noqa: F401
    validate_dimensions,  # noqa: F401
)
from gamedev_shared.validation import (
    validate_params as _validate_params,
)
from gamedev_shared.validation import (
    validate_prompt as _validate_prompt,
)


def validate_prompt(prompt: str, max_length: int = 1000) -> tuple[bool, str | None]:
    """Valida um prompt (default Text2Icon: 1000 caracteres).

    Returns:
        Tuple (is_valid, error_message).
    """
    return _validate_prompt(prompt, max_length=max_length)


def validate_params(params: dict[str, Any]) -> tuple[bool, str | None]:
    """Valida parâmetros de geração.

    Sana Sprint gera em 1-4 passos; o limite inferior é 1 (não 10 como no FLUX).

    Returns:
        Tuple (is_valid, error_message).
    """
    return _validate_params(
        params,
        min_steps=1,
        default_guidance=4.5,
        default_steps=2,
        default_width=512,
        default_height=512,
    )
