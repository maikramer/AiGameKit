"""Validação de parâmetros de geração — funções partilhadas parameterizáveis.

Extrai as 3 cópias quase-idênticas de ``validate_prompt`` / ``validate_dimensions``
/ ``validate_params`` que existiam em Texture2D/utils.py, Skymap2D/utils.py e
Text2Icon/utils.py. As diferenças (max_length, limites de dimensão, range de
passos, ratio 2:1 do equirect) são agora parâmetros com defaults sensatos.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


def validate_prompt(prompt: str, *, max_length: int = 500) -> tuple[bool, str | None]:
    """Valida um prompt de geração.

    Args:
        prompt: O texto do prompt.
        max_length: Comprimento máximo em caracteres (default 500; Text2Icon usa 1000).

    Returns:
        Tuple ``(is_valid, error_message)``. ``error_message`` é ``None`` se válido.
    """
    if not prompt or not prompt.strip():
        return False, "Prompt não pode ser vazio"

    if len(prompt) > max_length:
        return False, f"Prompt excede o limite de {max_length} caracteres"

    return True, None


def validate_dimensions(
    width: int,
    height: int,
    *,
    min_dim: int = 256,
    max_width: int = 2048,
    max_height: int = 2048,
    multiple_of: int = 8,
    warn_ratio: float | None = None,
    logger: Any = None,
) -> tuple[bool, str | None]:
    """Valida dimensões de imagem.

    Args:
        width: Largura em píxeis.
        height: Altura em píxeis.
        min_dim: Dimensão mínima (default 256).
        max_width: Largura máxima (default 2048; Skymap2D usa 4096).
        max_height: Altura máxima (default 2048).
        multiple_of: As dimensões devem ser múltiplas deste valor (default 8).
        warn_ratio: Se fornecido, emite um warning se o ratio width/height diferir
            deste valor por mais de 0.1 (ex: ``2.0`` para equirectangular 2:1).
        logger: Logger opcional para o warning de ratio (precisa de método ``.warning``).

    Returns:
        Tuple ``(is_valid, error_message)``. ``error_message`` é ``None`` se válido.
    """
    if width < min_dim or width > max_width:
        return False, f"Largura deve estar entre {min_dim} e {max_width}"

    if height < min_dim or height > max_height:
        return False, f"Altura deve estar entre {min_dim} e {max_height}"

    if width % multiple_of != 0 or height % multiple_of != 0:
        return False, f"Dimensões devem ser múltiplos de {multiple_of}"

    if warn_ratio is not None:
        ratio = width / height
        if abs(ratio - warn_ratio) > 0.1 and logger is not None:
            logger.warning(
                f"Ratio {ratio:.2f}:1 não é {warn_ratio}:1. Recomenda-se ratio {warn_ratio}:1 para melhores resultados."
            )

    return True, None


def validate_params(
    params: dict[str, Any],
    *,
    min_steps: int = 10,
    max_steps: int = 100,
    min_guidance: float = 1.0,
    max_guidance: float = 20.0,
    min_dim: int = 256,
    max_width: int = 2048,
    max_height: int = 2048,
    warn_ratio: float | None = None,
    logger: Any = None,
) -> tuple[bool, str | None]:
    """Valida parâmetros de geração (guidance, steps, dimensions).

    Args:
        params: Dict com chaves ``guidance_scale``, ``num_inference_steps``,
            ``width``, ``height`` (todas opcionais, com defaults sensatos).
        min_steps: Passos mínimos (default 10; Text2Icon/Sana Sprint usa 1).
        max_steps: Passos máximos (default 100).
        min_guidance: Guidance mínimo (default 1.0).
        max_guidance: Guidance máximo (default 20.0).
        min_dim, max_width, max_height, warn_ratio, logger: Ver ``validate_dimensions``.

    Returns:
        Tuple ``(is_valid, error_message)``.
    """
    guidance = params.get("guidance_scale", 7.5)
    if not min_guidance <= guidance <= max_guidance:
        return False, f"Guidance scale deve estar entre {min_guidance} e {max_guidance}"

    steps = params.get("num_inference_steps", 50)
    if not min_steps <= steps <= max_steps:
        return False, f"Número de passos deve estar entre {min_steps} e {max_steps}"

    width = params.get("width", 1024)
    height = params.get("height", 1024)
    is_valid, error = validate_dimensions(
        width,
        height,
        min_dim=min_dim,
        max_width=max_width,
        max_height=max_height,
        warn_ratio=warn_ratio,
        logger=logger,
    )
    if not is_valid:
        return False, error

    return True, None


def format_timestamp(timestamp: float) -> str:
    """Formata um timestamp Unix para string legível (YYYY-MM-DD HH:MM:SS)."""
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
