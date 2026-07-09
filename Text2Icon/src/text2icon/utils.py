"""Funções utilitárias para Text2Icon."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from gamedev_shared.gpu import format_bytes  # noqa: F401
from gamedev_shared.path_utils import ensure_directory  # noqa: F401
from gamedev_shared.seed_utils import generate_seed  # noqa: F401


def validate_prompt(prompt: str, max_length: int = 1000) -> tuple[bool, str | None]:
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
    """Valida dimensões de imagem.

    Returns:
        Tuple (is_valid, error_message).
    """
    min_dim = 256
    max_dim = 2048

    if width < min_dim or width > max_dim:
        return False, f"Largura deve estar entre {min_dim} e {max_dim}"

    if height < min_dim or height > max_dim:
        return False, f"Altura deve estar entre {min_dim} e {max_dim}"

    if width % 8 != 0 or height % 8 != 0:
        return False, "Dimensões devem ser múltiplos de 8"

    return True, None


def validate_params(params: dict[str, Any]) -> tuple[bool, str | None]:
    """Valida parâmetros de geração.

    Sana Sprint gera em 1-4 passos; o limite inferior é 1 (não 10 como no FLUX).

    Returns:
        Tuple (is_valid, error_message).
    """
    guidance = params.get("guidance_scale", 4.5)
    if not 1.0 <= guidance <= 20.0:
        return False, "Guidance scale deve estar entre 1.0 e 20.0"

    steps = params.get("num_inference_steps", 2)
    if not 1 <= steps <= 100:
        return False, "Número de passos deve estar entre 1 e 100"

    width = params.get("width", 512)
    height = params.get("height", 512)
    is_valid, error = validate_dimensions(width, height)
    if not is_valid:
        return False, error

    return True, None


def format_timestamp(timestamp: float) -> str:
    """Formata um timestamp Unix para string legível."""
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
