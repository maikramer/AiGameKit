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
from typing import Any, cast

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
    logger.info(f"Image saved to {filepath}")

    write_metadata_sidecar(filepath, prompt, params, metadata=metadata)
    return filepath


def write_metadata_sidecar(
    filepath: Path,
    prompt: str,
    params: dict[str, Any],
    *,
    metadata: dict[str, Any] | None = None,
) -> Path:
    """Write the canonical JSON metadata sidecar next to ``filepath``.

    Extracted from :func:`save_image_with_metadata` for flows that write the
    bitmap outside PIL (e.g. Skymap2D EXR export) but want the same metadata:
    timestamp, prompt, params, image_path, filename + extra keys.

    Args:
        filepath: Path of the asset (sidecar becomes ``<stem>.json``).
        prompt: Prompt used to generate the asset.
        params: Generation parameters (seed, steps, guidance, etc.).
        metadata: Extra keys merged into the sidecar JSON (win over base keys).

    Returns:
        Path to the written sidecar JSON.
    """
    metadata_path = filepath.with_suffix(".json")
    metadata_dict: dict[str, Any] = {
        "timestamp": datetime.now().timestamp(),
        "prompt": prompt,
        "params": params,
        "image_path": str(filepath),
        "filename": filepath.name,
    }
    if metadata:
        metadata_dict.update(metadata)

    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(metadata_dict, f, indent=2, ensure_ascii=False)
    return metadata_path


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
    logger.info(f"ZIP created at {zip_path}")
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
            return cast(dict[str, Any] | None, json.load(f))
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

    Delega a ``aigamekit_shared.path_utils.safe_filename`` com ``separator="_"``
    (underscores, para consistência com metadata sidecars existentes).

    Args:
        text: Raw text to sanitize.
        max_length: Maximum length of the returned string.

    Returns:
        A sanitized, lowercased filename stem (no extension).
    """
    from .path_utils import safe_filename as _safe

    return _safe(text, max_len=max_length, separator="_")
