#!/usr/bin/env python3
"""Instalador Rocks3D — delega ao Clified."""

from __future__ import annotations

import sys
from pathlib import Path

_shared_src = Path(__file__).resolve().parents[2] / "Shared" / "src"
if _shared_src.is_dir() and str(_shared_src) not in sys.path:
    sys.path.insert(0, str(_shared_src))

from gamedev_shared.installer.tool_script import run_fixed_tool

if __name__ == "__main__":
    sys.exit(run_fixed_tool("rocks3d", description="Instalador Rocks3D"))
