"""Path utilities for output file handling."""

from __future__ import annotations

import re
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


def safe_filename(text: str, max_len: int = 40) -> str:
    """Convert *text* to a filesystem-safe filename stem.

    Args:
        text: Input text to sanitize.
        max_len: Maximum length of the result.

    Returns:
        Lowercase, hyphenated, alphanumeric-only string.
    """
    safe = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    safe = re.sub(r"[\s_]+", "-", safe)
    return safe[:max_len]
