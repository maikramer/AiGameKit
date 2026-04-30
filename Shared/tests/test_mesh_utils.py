"""Tests for gamedev_shared.mesh_utils — weld_glb no-op."""

from __future__ import annotations

import tempfile
from pathlib import Path

from gamedev_shared.mesh_utils import weld_glb


class TestWeldGlbNoop:
    def test_weld_glb_is_noop(self) -> None:
        weld_glb("/nonexistent/path/test.glb")

    def test_weld_glb_does_not_modify_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            glb_path = Path(tmpdir) / "test.glb"
            glb_path.write_bytes(b"fake glb")
            size_before = glb_path.stat().st_size
            weld_glb(glb_path)
            size_after = glb_path.stat().st_size
            assert size_after == size_before
