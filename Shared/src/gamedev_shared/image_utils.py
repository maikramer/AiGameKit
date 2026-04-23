"""Shared image utilities for Texture2D, Skymap2D, and other image-producing packages.

Provides common helpers for saving images with JSON metadata sidecars,
creating thumbnails, zipping files, and basic PIL image conversions.
"""

from __future__ import annotations

import io
import json
import logging
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image

logger = logging.getLogger(__name__)

_FORMAT_TO_EXT: dict[str, str] = {
    "PNG": ".png",
    "JPEG": ".jpg",
    "WEBP": ".webp",
    "BMP": ".bmp",
    "TIFF": ".tiff",
    "GIF": ".gif",
}


def save_image_with_metadata(
    image: Image.Image,
    prompt: str,
    params: dict[str, Any],
    output_dir: Path,
    filename: str | None = None,
    metadata: dict[str, Any] | None = None,
    *,
    image_format: str = "PNG",
) -> Path:
    """Save a PIL image and write a JSON sidecar with generation metadata.

    Args:
        image: PIL image to save.
        prompt: Prompt used to generate the image.
        params: Generation parameters (seed, steps, guidance, etc.).
        output_dir: Directory where the image will be written (created if missing).
        filename: Output filename. Auto-generated from timestamp if ``None``.
        metadata: Extra keys merged into the sidecar JSON.
        image_format: PIL format string (e.g. ``"PNG"``, ``"JPEG"``).

    Returns:
        Path to the saved image file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    if filename is None:
        ts = int(datetime.now().timestamp())
        ext = _FORMAT_TO_EXT.get(image_format.upper(), ".png")
        filename = f"image_{ts}{ext}"

    filepath = output_dir / filename
    image.save(filepath, image_format)
    logger.info("Image saved to %s", filepath)

    metadata_path = filepath.with_suffix(".json")
    metadata_dict: dict[str, Any] = {
        "timestamp": datetime.now().timestamp(),
        "prompt": prompt,
        "params": params,
        "image_path": str(filepath),
        "filename": filename,
    }
    if metadata:
        metadata_dict.update(metadata)

    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(metadata_dict, f, indent=2, ensure_ascii=False)

    return filepath


def create_thumbnail(image: Image.Image, size: tuple[int, int] = (256, 256)) -> Image.Image:
    """Return a resized copy of *image* fitted within *size*.

    Args:
        image: Source PIL image.
        size: Maximum ``(width, height)`` for the thumbnail.

    Returns:
        New image with the thumbnail applied.
    """
    thumb = image.copy()
    thumb.thumbnail(size, Image.Resampling.LANCZOS)
    return thumb


def create_zip(files: list[Path], zip_path: Path) -> Path:
    """Create a ZIP archive containing *files*.

    Only files that exist on disk are included.

    Args:
        files: Paths of files to add.
        zip_path: Destination path for the ZIP archive.

    Returns:
        The *zip_path* after writing.
    """
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for file in files:
            if file.exists():
                zipf.write(file, file.name)
    logger.info("ZIP created at %s", zip_path)
    return zip_path


def load_image_metadata(image_path: Path) -> dict[str, Any] | None:
    """Load JSON metadata sidecar for *image_path*.

    Looks for ``<stem>.json`` next to *image_path*.

    Args:
        image_path: Path to the image file.

    Returns:
        Parsed metadata dict, or ``None`` if the sidecar does not exist or
        cannot be decoded.
    """
    metadata_path = image_path.with_suffix(".json")
    if not metadata_path.exists():
        return None
    try:
        with open(metadata_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error("Failed to load metadata: %s", e)
        return None


def load_bytes_as_rgb(raw_bytes: bytes) -> Image.Image:
    """Decode raw image bytes into an RGB PIL image.

    Args:
        raw_bytes: Bytes of an image file (PNG, JPEG, etc.).

    Returns:
        An RGB-mode PIL image.
    """
    return Image.open(io.BytesIO(raw_bytes)).convert("RGB")


def ensure_rgb(image: Image.Image) -> Image.Image:
    """Return *image* converted to RGB if it is not already.

    Args:
        image: Source PIL image (any mode).

    Returns:
        The same image if already RGB, otherwise a new RGB conversion.
    """
    if image.mode == "RGB":
        return image
    return image.convert("RGB")


def safe_filename(text: str, max_length: int = 80) -> str:
    """Sanitize *text* into a filesystem-safe filename.

    Replaces characters that are problematic on common filesystems with
    underscores and truncates to *max_length*.

    Args:
        text: Raw text to sanitize.
        max_length: Maximum length of the returned string.

    Returns:
        A sanitized, lowercased filename stem (no extension).
    """
    import re

    safe = re.sub(r"[^\w\s-]", "_", text).strip().lower()
    safe = re.sub(r"[\s_]+", "_", safe)
    return safe[:max_length]
