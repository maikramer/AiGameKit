"""Utilitários de processamento de imagem para Text2Icon.

Image I/O and metadata helpers são delegados a ``aigamekit_shared.image_utils``.
Preserva o modo RGBA (alpha de ícones transparentes) quando aplicável.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image

from aigamekit_shared.image_utils import (
    save_image_with_metadata,
)

DEFAULT_OUTPUT_DIR = Path("outputs") / "icons"


def save_image(
    image: Image.Image,
    prompt: str,
    params: dict[str, Any],
    output_dir: Path | None = None,
    filename: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Path:
    """Grava uma imagem (PNG, com alpha se aplicável) com metadata JSON ao lado.

    Returns:
        Path do ficheiro PNG gravado.
    """
    # image_format="PNG" preserva o canal alpha de ícones transparentes (RGBA).
    return save_image_with_metadata(
        image,
        prompt,
        params,
        output_dir=output_dir or DEFAULT_OUTPUT_DIR,
        filename=filename,
        metadata=metadata,
        image_format="PNG",
    )
