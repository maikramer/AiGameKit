"""Utilitários de processamento de imagem para Skymap2D.

Image I/O and metadata helpers are imported from ``gamedev_shared.image_utils``.
This module re-exports them for backward compatibility and adds Skymap2D-specific
defaults (EXR export, 2:1 thumbnail ratio).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image

from gamedev_shared.image_utils import save_image_with_metadata

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = Path("outputs") / "skymaps"


def save_image(
    image: Image.Image,
    prompt: str,
    params: dict[str, Any],
    output_dir: Path | None = None,
    filename: str | None = None,
    metadata: dict[str, Any] | None = None,
    *,
    image_format: str = "png",
    exr_scale: float = 1.0,
) -> Path:
    """Grava uma imagem com metadata JSON ao lado.

    ``image_format``: ``png`` (8-bit sRGB) ou ``exr`` (RGB float32 linear, mesmo
    conteúdo que o PNG após descodificação sRGB — ver ``exr_export``).

    Returns:
        Path do ficheiro gravado (.png ou .exr).
    """
    out_dir = output_dir or DEFAULT_OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    fmt = (image_format or "png").lower()
    if fmt not in ("png", "exr"):
        raise ValueError(f"image_format deve ser png ou exr, recebido: {image_format}")

    if fmt == "exr":
        from .exr_export import pil_rgb_to_linear_f32, write_exr_rgb_linear

        if filename is None:
            ts = int(datetime.now().timestamp())
            filename = f"skymap_{ts}.exr"
        filepath = out_dir / filename
        linear = pil_rgb_to_linear_f32(image)
        write_exr_rgb_linear(filepath, linear, scale=exr_scale)
        logger.info("EXR gravado em %s", filepath)
    else:
        return save_image_with_metadata(
            image,
            prompt,
            params,
            output_dir=out_dir,
            filename=filename,
            metadata=metadata,
            image_format="PNG",
        )

    metadata_path = filepath.with_suffix(".json")
    metadata_dict: dict[str, Any] = {
        "timestamp": datetime.now().timestamp(),
        "prompt": prompt,
        "params": params,
        "image_path": str(filepath),
        "filename": filename,
        "image_format": fmt,
        "color_space": "linear_rgb" if fmt == "exr" else "srgb_png",
    }
    if metadata:
        metadata_dict.update(metadata)

    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(metadata_dict, f, indent=2, ensure_ascii=False)

    return filepath


def create_thumbnail(image: Image.Image, size: tuple[int, int] = (512, 256)) -> Image.Image:
    """Cria um thumbnail da imagem (2:1 por defeito para skymaps)."""
    from gamedev_shared.image_utils import create_thumbnail as _create_thumbnail

    return _create_thumbnail(image, size)
