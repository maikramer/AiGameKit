"""Testes do pós-processo Paint3D (smooth/upscale/origin)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# paint3d.__init__ puxa texture_upscale → bpy; stub antes do import.
sys.modules.setdefault("bpy", MagicMock())

from paint3d.postprocess import apply_paint_postprocess  # noqa: E402


class TestApplyPaintPostprocess:
    def test_noop_returns_empty(self, tmp_path: Path) -> None:
        glb = tmp_path / "x.glb"
        glb.write_bytes(b"glb")
        assert apply_paint_postprocess(glb) == {}

    def test_smooth_calls_pipeline(self, tmp_path: Path) -> None:
        glb = tmp_path / "x.glb"
        glb.write_bytes(b"glb")
        mesh = MagicMock()
        fake_smooth = MagicMock()
        fake_smooth.smooth_trimesh_texture = MagicMock(return_value=mesh)
        with (
            patch.dict(sys.modules, {"paint3d.texture_smooth": fake_smooth}),
            patch("paint3d.utils.mesh_io.load_mesh_trimesh", return_value=mesh),
            patch("paint3d.utils.mesh_io.save_glb") as save,
        ):
            out = apply_paint_postprocess(glb, smooth=True, smooth_passes=2)
        assert out["smooth"] is True
        assert out["smooth_passes"] == 2
        fake_smooth.smooth_trimesh_texture.assert_called_once()
        save.assert_called_once()

    def test_preserve_origin_fits_aabb(self, tmp_path: Path) -> None:
        glb = tmp_path / "out.glb"
        ref = tmp_path / "ref.glb"
        glb.write_bytes(b"o")
        ref.write_bytes(b"r")
        with patch("paint3d.painter._fit_glb_aabb_to_reference") as fit:
            out = apply_paint_postprocess(glb, mesh_path=ref, preserve_origin=True)
        assert out["preserve_origin"] is True
        fit.assert_called_once()
