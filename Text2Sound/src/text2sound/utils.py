"""Text2Sound — utilitários comuns."""

from __future__ import annotations

import time
from pathlib import Path

from gamedev_shared.path_utils import safe_filename
from gamedev_shared.seed_utils import resolve_effective_seed  # noqa: F401


def format_bytes(bytes_val: int | float) -> str:
    val = float(bytes_val)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if val < 1024.0:
            return f"{val:.1f} {unit}"
        val /= 1024.0
    return f"{val:.1f} PB"


def generate_output_path(
    prompt: str,
    output_dir: Path,
    fmt: str = "wav",
) -> Path:
    """Gera caminho de saída único baseado no prompt e timestamp."""
    ts = int(time.time())
    safe = safe_filename(prompt)
    return output_dir / f"{safe}_{ts}.{fmt}"


def format_duration(seconds: float) -> str:
    """Formata segundos como mm:ss."""
    m = int(seconds) // 60
    s = int(seconds) % 60
    return f"{m}:{s:02d}"
