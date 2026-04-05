"""Skip helpers when torchaudio/stable_audio native bits cannot load (CPU-only / missing CUDA libs)."""

from __future__ import annotations

import pytest


def require_audio_stack() -> None:
    """Skip if torch + torchaudio + stable_audio_tools are not usable in this environment."""
    pytest.importorskip("torch")
    try:
        import torchaudio  # noqa: F401
    except OSError as exc:
        pytest.skip(f"torchaudio extension not loadable: {exc}", allow_module_level=True)
    try:
        import stable_audio_tools  # noqa: F401
    except (ImportError, OSError) as exc:
        pytest.skip(f"stable_audio_tools not available: {exc}", allow_module_level=True)
