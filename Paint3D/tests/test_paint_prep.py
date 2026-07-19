"""Tests for paint_prep (inpaint mask restriction + bake supersampling — no GPU)."""

from __future__ import annotations

import os
from unittest.mock import patch

import numpy as np


class TestComputeBakeSubdivLevels:
    def test_low_poly_gets_two_levels(self) -> None:
        from paint3d.paint_prep import compute_bake_subdiv_levels

        # 160k * 6 = 960k < 2M → nível 2 (3.8M)
        assert compute_bake_subdiv_levels(160_000) == 2

    def test_mid_poly_gets_one_level(self) -> None:
        from paint3d.paint_prep import compute_bake_subdiv_levels

        # 500k * 6 = 3M >= 2M → nível 1
        assert compute_bake_subdiv_levels(500_000) == 1

    def test_high_poly_skips(self) -> None:
        from paint3d.paint_prep import compute_bake_subdiv_levels

        assert compute_bake_subdiv_levels(2_270_000) == 0

    def test_zero_faces(self) -> None:
        from paint3d.paint_prep import compute_bake_subdiv_levels

        assert compute_bake_subdiv_levels(0) == 0

    def test_max_levels_cap(self) -> None:
        from paint3d.paint_prep import compute_bake_subdiv_levels

        assert compute_bake_subdiv_levels(1_000, max_levels=2) == 2
        assert compute_bake_subdiv_levels(1_000, max_levels=4) == 4


class TestInstallBakeSupersampling:
    class _FakeRender:
        def __init__(self) -> None:
            self.load_calls: list = []
            self.save_calls: list = []
            self.set_mesh_calls: list = []
            self.load_mesh = self._load_mesh
            self.save_mesh = self._save_mesh

        def _load_mesh(self, mesh=None, **kw):
            self.load_calls.append(mesh)

        def _save_mesh(self, path, downsample=False):
            self.save_calls.append(path)

        def set_mesh(self, vtx_pos, pos_idx, vtx_uv=None, uv_idx=None, **kw):
            self.set_mesh_calls.append((vtx_pos, pos_idx))

    def test_env_disable(self) -> None:
        from paint3d.paint_prep import install_bake_supersampling

        render = self._FakeRender()
        orig = render.load_mesh
        with patch.dict(os.environ, {"PAINT3D_BAKE_SUBDIV": "0"}):
            install_bake_supersampling(render)
        assert render.load_mesh is orig

    def test_save_restores_original_mesh(self) -> None:
        from paint3d import paint_prep
        from paint3d.paint_prep import install_bake_supersampling

        render = self._FakeRender()
        vtx = np.zeros((3, 3), dtype=np.float32)
        idx = np.zeros((160_000, 3), dtype=np.int32)

        with (
            patch.dict(os.environ, {"PAINT3D_BAKE_SUBDIV": ""}),
            patch.object(paint_prep, "subdivide_bake_mesh") as sub,
            patch(
                "paint3d.hy3dpaint.DifferentiableRenderer.mesh_utils.load_mesh",
                return_value=(vtx, idx, vtx[:, :2], idx, None),
            ),
        ):
            install_bake_supersampling(render)
            render.load_mesh(mesh=object())
            assert sub.call_count == 1
            assert sub.call_args[0][1] == 2  # levels
            render.save_mesh("/tmp/out.glb")

        assert len(render.set_mesh_calls) == 1
        assert render.save_calls == ["/tmp/out.glb"]

    def test_fallback_on_extract_error(self) -> None:
        from paint3d.paint_prep import install_bake_supersampling

        render = self._FakeRender()
        with (
            patch.dict(os.environ, {"PAINT3D_BAKE_SUBDIV": ""}),
            patch(
                "paint3d.hy3dpaint.DifferentiableRenderer.mesh_utils.load_mesh",
                side_effect=RuntimeError("boom"),
            ),
        ):
            install_bake_supersampling(render)
            render.load_mesh(mesh=object())
            render.save_mesh("/tmp/out.glb")
        assert len(render.set_mesh_calls) == 0
        assert render.load_calls and render.save_calls


def test_restrict_inpaint_mask_keeps_far_holes() -> None:
    from paint3d.paint_prep import restrict_inpaint_mask

    mask = np.zeros((32, 32), dtype=np.uint8)
    mask[10:20, 10:20] = 255
    new_mask, far = restrict_inpaint_mask(mask, dilate_px=2)
    assert far[0, 0]
    assert not far[15, 15]
    assert new_mask[15, 15] == 255
    # Near the trusted blob, zeros become inpaint targets (0)
    assert (new_mask == 0).any()
