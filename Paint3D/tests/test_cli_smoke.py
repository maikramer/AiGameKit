"""Testes smoke para o CLI Paint3D (não requer GPU)."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

_PAINT3D_ROOT = Path(__file__).resolve().parents[1]
_SRC = _PAINT3D_ROOT / "src"
_SHARED_SRC = _PAINT3D_ROOT.parent / "Shared" / "src"


def _paint3d_env() -> dict[str, str]:
    sep = os.pathsep
    pp = f"{_SRC}{sep}{_SHARED_SRC}"
    return {
        **os.environ,
        "PYTHONPATH": pp + sep + os.environ.get("PYTHONPATH", ""),
        "PYTHONIOENCODING": "utf-8",
    }


def test_paint3d_help():
    pytest.importorskip("torch")
    r = subprocess.run(
        [sys.executable, "-m", "paint3d", "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        cwd=str(_PAINT3D_ROOT),
        env=_paint3d_env(),
    )
    assert r.returncode == 0
    out = r.stdout.lower()
    assert "paint3d" in out or "texture" in out
    assert "2.1" in out or "hunyuan" in out


def test_paint3d_version():
    pytest.importorskip("torch")
    r = subprocess.run(
        [sys.executable, "-m", "paint3d", "--version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        cwd=str(_PAINT3D_ROOT),
        env=_paint3d_env(),
    )
    assert r.returncode == 0
    assert "0.1.0" in r.stdout
