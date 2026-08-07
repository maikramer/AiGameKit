"""Ensure vendored ``hymotion`` is importable as top-level ``hymotion``."""

from __future__ import annotations

import sys
from pathlib import Path

_VENDOR_DIR = Path(__file__).resolve().parent / "vendor"
_BOOTSTRAPPED = False


def ensure_hymotion_on_path() -> Path:
    """Put ``motion3d/vendor`` on ``sys.path`` so ``import hymotion`` works.

    Upstream configs use module paths like ``hymotion/pipeline/...``.
    """
    global _BOOTSTRAPPED
    vendor = _VENDOR_DIR
    vendor_str = str(vendor)
    if vendor_str not in sys.path:
        sys.path.insert(0, vendor_str)
    _BOOTSTRAPPED = True
    return vendor


def hymotion_stats_dir() -> Path:
    """Mean/Std for HY-Motion latent (201-dim)."""
    return _VENDOR_DIR / "hymotion_assets" / "stats"


def hymotion_package_dir() -> Path:
    return _VENDOR_DIR / "hymotion"
