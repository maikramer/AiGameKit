"""Funções utilitárias para Skymap2D."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from gamedev_shared.gpu import format_bytes  # noqa: F401
from gamedev_shared.logging import Logger as _Logger
from gamedev_shared.path_utils import ensure_directory  # noqa: F401
from gamedev_shared.seed_utils import generate_seed  # noqa: F401

_logger = _Logger("skymap2d.utils")


def validate_prompt(prompt: str, max_length: int = 500) -> tuple[bool, str | None]:
    """Valida um prompt.

    Returns:
        Tuple (is_valid, error_message).
    """
    if not prompt or not prompt.strip():
        return False, "Prompt não pode ser vazio"

    if len(prompt) > max_length:
        return False, f"Prompt excede o limite de {max_length} caracteres"

    return True, None


def validate_dimensions(width: int, height: int) -> tuple[bool, str | None]:
    """Valida dimensões de imagem para skymap equirectangular.

    Recomenda ratio 2:1 com tolerância de 5%.

    Returns:
        Tuple (is_valid, error_message).
    """
    min_dim = 256
    max_w = 4096
    max_h = 2048

    if width < min_dim or width > max_w:
        return False, f"Largura deve estar entre {min_dim} e {max_w}"

    if height < min_dim or height > max_h:
        return False, f"Altura deve estar entre {min_dim} e {max_h}"

    if width % 8 != 0 or height % 8 != 0:
        return False, "Dimensões devem ser múltiplos de 8"

    ratio = width / height
    if abs(ratio - 2.0) > 0.1:
        _logger.warning(
            f"Ratio {ratio:.2f}:1 não é 2:1. Skymaps equirectangular funcionam melhor com ratio 2:1 (ex: 2048x1024)."
        )

    return True, None


def validate_params(params: dict[str, Any]) -> tuple[bool, str | None]:
    """Valida parâmetros de geração de skymap.

    Returns:
        Tuple (is_valid, error_message).
    """
    guidance = params.get("guidance_scale", 6.0)
    if not 1.0 <= guidance <= 20.0:
        return False, "Guidance scale deve estar entre 1.0 e 20.0"

    steps = params.get("num_inference_steps", 40)
    if not 10 <= steps <= 100:
        return False, "Número de passos deve estar entre 10 e 100"

    width = params.get("width", 2048)
    height = params.get("height", 1024)
    is_valid, error = validate_dimensions(width, height)
    if not is_valid:
        return False, error

    return True, None


def format_timestamp(timestamp: float) -> str:
    """Formata um timestamp Unix para string legível."""
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
