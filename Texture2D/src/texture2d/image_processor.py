"""Utilitários de processamento de imagem para Texture2D.

Image I/O and metadata helpers are imported from ``gamedev_shared.image_utils``.
This module re-exports them for backward compatibility and adds Texture2D-specific
defaults.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image

from gamedev_shared.image_utils import (
    save_image_with_metadata,
)

DEFAULT_OUTPUT_DIR = Path("outputs") / "textures"


def save_image(
    image: Image.Image,
    prompt: str,
    params: dict[str, Any],
    output_dir: Path | None = None,
    filename: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Path:
    """Grava uma imagem com metadata JSON ao lado.

    Returns:
        Path do ficheiro PNG gravado.
    """
    return save_image_with_metadata(
        image,
        prompt,
        params,
        output_dir=output_dir or DEFAULT_OUTPUT_DIR,
        filename=filename,
        metadata=metadata,
        image_format="PNG",
    )
