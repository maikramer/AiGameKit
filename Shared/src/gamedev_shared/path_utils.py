"""Path utilities for output file handling."""

from __future__ import annotations

import re
import time
from pathlib import Path


def ensure_directory(path: Path) -> Path:
    """Create *path* directory (with parents) if it doesn't exist.

    Args:
        path: Directory path to create.

    Returns:
        The same *path* for chaining.
    """
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_filename(text: str, max_len: int = 40, *, separator: str = "-") -> str:
    """Convert *text* to a filesystem-safe filename stem.

    Args:
        text: Input text to sanitize.
        max_len: Maximum length of the result.
        separator: Character to join words (default ``-`` for hyphens;
            ``_`` for underscores — usado por image_utils).

    Returns:
        Lowercase, alphanumeric-only string joined by *separator*.
    """
    safe = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    safe = re.sub(r"[\s_-]+", separator, safe)
    return safe[:max_len]


def generate_output_path(
    prompt: str,
    output_dir: Path,
    fmt: str = "png",
    *,
    separator: str = "-",
    max_len: int = 40,
) -> Path:
    """Gera um path de saída único baseado no prompt + timestamp.

    Args:
        prompt: Texto do prompt (sanitizado para filename).
        output_dir: Diretório de saída.
        fmt: Extensão/formato do ficheiro (ex: ``png``, ``ogg``, ``glb``).
        separator: Separador de palavras (ver ``safe_filename``).
        max_len: Comprimento máximo do filename stem.

    Returns:
        ``output_dir / f"{safe}_{ts}.{fmt}"``.
    """
    ts = int(time.time())
    safe = safe_filename(prompt, max_len=max_len, separator=separator)
    return output_dir / f"{safe}_{ts}.{fmt}"
