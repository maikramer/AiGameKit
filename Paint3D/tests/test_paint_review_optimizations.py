"""Optimizações da revisão Paint3D (2026-10-30) — contrato com o pipeline."""

from __future__ import annotations

import numpy as np
import torch
from PIL import Image

from paint3d.hy3dpaint.utils.pipeline_utils import (
    ViewProcessor,
    merge_albedo_mr_views,
    split_baked_albedo_mr,
)


def test_merge_albedo_mr_views_seis_canais() -> None:
    """Albedo (3ch) + mr (3ch) por vista → 6ch float32 0..1, canais na ordem."""
    albedo = [Image.new("RGB", (4, 3), (255, 0, 0)), Image.new("RGB", (4, 3), (0, 255, 0))]
    mr = [Image.new("RGB", (4, 3), (0, 0, 255)), Image.new("RGB", (4, 3), (10, 20, 30))]
    merged = merge_albedo_mr_views(albedo, mr)
    assert len(merged) == 2
    assert merged[0].shape == (3, 4, 6)  # H, W, C
    assert merged[0].dtype == np.float32
    assert np.isclose(merged[0][..., 0].max(), 1.0)  # R do albedo
    assert np.isclose(merged[0][..., 5].max(), 1.0)  # B do mr
    # Mr fica nos canais 3..5, sem misturar com o albedo.
    assert np.isclose(merged[1][..., 3].mean(), 10 / 255)
    assert np.isclose(merged[1][..., 4].mean(), 20 / 255)
    assert np.isclose(merged[1][..., 5].mean(), 30 / 255)


def test_merge_aceita_tons_de_cinza() -> None:
    """Vistas L (não esperado no PBR, mas robustez) são replicadas em RGB."""
    albedo = [Image.new("L", (2, 2), 128)]
    mr = [Image.new("L", (2, 2), 64)]
    merged = merge_albedo_mr_views(albedo, mr)
    assert merged[0].shape == (2, 2, 6)
    assert np.isclose(merged[0][..., 0].mean(), 128 / 255)
    assert np.isclose(merged[0][..., 5].mean(), 64 / 255)


def test_split_baked_separa_e_contiguo() -> None:
    texture6 = torch.zeros(2, 2, 6)
    texture6[..., :3] = 0.25
    texture6[..., 3:] = 0.75
    albedo, mr = split_baked_albedo_mr(texture6)
    assert albedo.shape == (2, 2, 3) and mr.shape == (2, 2, 3)
    assert albedo.is_contiguous() and mr.is_contiguous()
    assert torch.allclose(albedo, torch.full((2, 2, 3), 0.25))
    assert torch.allclose(mr, torch.full((2, 2, 3), 0.75))


class _StubRender:
    """Render mínimo para exercitar a selecção de vistas sem GPU."""

    def __init__(self, resolution: int = 1024) -> None:
        self.default_resolution = resolution
        self.resolutions: list[int] = []
        self.unreliable = None

    def set_default_render_resolution(self, resolution) -> None:
        self.resolutions.append(int(resolution))
        self.default_resolution = int(resolution)

    def get_face_areas(self, from_one_index: bool = False) -> np.ndarray:
        return np.ones(16, dtype=np.float32)

    def set_boundary_unreliable_scale(self, scale) -> None:
        self.unreliable = scale

    def render_alpha(self, elev, azim, return_type="np") -> np.ndarray:
        idx = np.zeros((1, 8, 8, 1), dtype=np.int32)
        idx[:, 2:6, 2:6, 0] = (abs(int(azim)) % 8) + 1
        return idx


def _select(render: _StubRender):
    vp = ViewProcessor(config=None, render=render)
    return vp.bake_view_selection(
        [0, 0, 0, 0, 0, 0],
        [0, 90, 180, 270, 0, 180],
        [1.0, 0.25, 0.7, 0.25, 0.05, 0.05],
        6,
    )


def test_seleccao_de_vistas_corre_a_512_e_restaura() -> None:
    render = _StubRender(1024)
    _select(render)
    assert render.resolutions[0] == 512
    assert render.resolutions[-1] == 1024


def test_seleccao_nao_sobe_acima_do_render_pedido() -> None:
    render = _StubRender(256)
    _select(render)
    assert render.resolutions[0] == 256
    assert render.resolutions[-1] == 256


def test_gutter_de_costura_cresce_com_a_resolucao() -> None:
    from paint3d.hy3dpaint.DifferentiableRenderer.mesh_utils import _seam_dilation_pixels

    assert _seam_dilation_pixels(512) == 4
    assert _seam_dilation_pixels(1024) == 4
    assert _seam_dilation_pixels(2048) == 8
    assert _seam_dilation_pixels(4096) == 16


def test_xatlas_worker_parametriza() -> None:
    """O worker do subprocesso devolve vmapping/indices/uvs válidos."""
    import subprocess
    import sys
    import tempfile
    from pathlib import Path

    import xatlas  # noqa: F401 — garante o extra instalado

    verts = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=np.float32)
    faces = np.array([[0, 1, 2], [0, 1, 3], [0, 2, 3], [1, 2, 3]], dtype=np.uint32)
    with tempfile.TemporaryDirectory() as td:
        inp = Path(td) / "in.npz"
        out = Path(td) / "out.npz"
        np.savez(inp, verts=verts, faces=faces)
        subprocess.run(
            [sys.executable, "-m", "paint3d.hy3dpaint.utils._xatlas_worker", str(inp), str(out)],
            check=True,
            timeout=60,
            capture_output=True,
        )
        data = np.load(out)
        # xatlas duplica vértices por chart (o tetraedro dá 4 charts).
        n_new = data["vmapping"].shape[0]
        assert n_new >= len(verts)
        assert data["indices"].shape == (len(faces), 3)
        assert data["uvs"].shape == (n_new, 2)
        assert np.isfinite(data["uvs"]).all()


def test_enhance_mr_desligado_por_defeito(monkeypatch) -> None:
    from paint3d.hy3dpaint.textureGenPipeline import Hunyuan3DPaintConfig

    monkeypatch.delenv("PAINT3D_ENHANCE_MR", raising=False)
    assert Hunyuan3DPaintConfig(6, 512).enhance_mr is False
    monkeypatch.setenv("PAINT3D_ENHANCE_MR", "1")
    assert Hunyuan3DPaintConfig(6, 512).enhance_mr is True
