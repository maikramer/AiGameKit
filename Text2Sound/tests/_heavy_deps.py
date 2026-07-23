"""Skip helpers when torchaudio/stable_audio native bits cannot load (CPU-only / missing CUDA libs)."""

from __future__ import annotations

import subprocess
import sys

import pytest


def _probe_import(module: str) -> str | None:
    """Import ``module`` in a child process.

    Returns ``None`` on success, else a short reason (crash / ImportError / OSError).
    Used because pedalboard (and similar) can SIGILL on some CI CPUs — that kills
    the pytest process if imported in-process.
    """
    proc = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode == 0:
        return None
    err = (proc.stderr or proc.stdout or "").strip().splitlines()
    tail = err[-1] if err else f"exit {proc.returncode}"
    return tail[:200]


def require_audio_stack() -> None:
    """Skip if torch + torchaudio + stable_audio_tools are not usable in this environment."""
    pytest.importorskip("torch")
    reason = _probe_import("torchaudio")
    if reason is not None:
        pytest.skip(f"torchaudio extension not loadable: {reason}", allow_module_level=True)
    reason = _probe_import("stable_audio_tools")
    if reason is not None:
        pytest.skip(f"stable_audio_tools not available: {reason}", allow_module_level=True)


def require_mastering_stack() -> None:
    """Skip if the DSP mastering deps (pedalboard + pyloudnorm) are unavailable.

    These are needed for the advanced mastering-chain tests; the core
    audio_processor tests only need torch + soundfile.

    Pedalboard wheels may SIGILL on hosted runners (unsupported CPU ISA) —
    probe in a subprocess so pytest survives.
    """
    for mod in ("pedalboard", "pyloudnorm"):
        reason = _probe_import(mod)
        if reason is not None:
            pytest.skip(f"{mod} not usable: {reason}")
