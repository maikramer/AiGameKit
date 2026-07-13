"""Text2Sound — utilitários comuns."""

from __future__ import annotations

from pathlib import Path

from gamedev_shared.gpu import format_bytes  # noqa: F401
from gamedev_shared.path_utils import generate_output_path as _generate_output_path  # noqa: F401
from gamedev_shared.seed_utils import resolve_effective_seed  # noqa: F401


def generate_output_path(
    prompt: str,
    output_dir: Path,
    fmt: str = "ogg",
) -> Path:
    """Gera caminho de saída único baseado no prompt e timestamp.

    Delega a ``gamedev_shared.path_utils.generate_output_path``.
    """
    return _generate_output_path(prompt, output_dir, fmt)


def format_duration(seconds: float) -> str:
    """Formata segundos como mm:ss."""
    m = int(seconds) // 60
    s = int(seconds) % 60
    return f"{m}:{s:02d}"
