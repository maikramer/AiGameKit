"""Testes unitários das melhorias de qualidade e perfil 6 GB FP16 do Paint3D."""

from __future__ import annotations

import numpy as np
import pytest
import torch

from paint3d.hy3dpaint.utils.multiview_utils import _build_scheduler
from paint3d.hy3dpaint.utils.pipeline_utils import ViewProcessor
from paint3d.mesh_denoise import (
    _face_connected_components,
    bilateral_normal_denoise,
    compute_welded_crease_normals,
)
from paint3d.painter import _want_sdnq_quant


def _make_cube() -> tuple[np.ndarray, np.ndarray]:
    verts = np.array(
        [
            [-1, -1, -1],
            [1, -1, -1],
            [1, 1, -1],
            [-1, 1, -1],
            [-1, -1, 1],
            [1, -1, 1],
            [1, 1, 1],
            [-1, 1, 1],
        ],
        dtype=np.float32,
    )
    faces = np.array(
        [
            [0, 2, 1],
            [0, 3, 2],
            [4, 5, 6],
            [4, 6, 7],
            [0, 1, 5],
            [0, 5, 4],
            [2, 3, 7],
            [2, 7, 6],
            [0, 4, 7],
            [0, 7, 3],
            [1, 2, 6],
            [1, 6, 5],
        ],
        dtype=np.int32,
    )
    return verts, faces


def _make_noisy_grid(n: int = 8, amp: float = 0.04) -> tuple[np.ndarray, np.ndarray]:
    xs, ys = np.meshgrid(np.linspace(0, 1, n), np.linspace(0, 1, n), indexing="ij")
    rng = np.random.default_rng(7)
    zs = rng.normal(0.0, amp, size=(n, n))
    verts = np.stack([xs, ys, zs], axis=-1).reshape(-1, 3).astype(np.float32)
    faces = []
    for i in range(n - 1):
        for j in range(n - 1):
            a = i * n + j
            b = (i + 1) * n + j
            c = (i + 1) * n + (j + 1)
            d = i * n + (j + 1)
            faces.append([a, b, c])
            faces.append([a, c, d])
    return verts, np.asarray(faces, dtype=np.int32)


def test_bilateral_denoise_flattens_micro_noise_and_respects_max_disp() -> None:
    verts, faces = _make_noisy_grid(n=8, amp=0.03)
    out = bilateral_normal_denoise(verts, faces, normal_iters=6, vertex_iters=12, max_disp_frac=0.05)
    assert out.shape == verts.shape
    # A variância em Z (ruído fora do plano) deve cair de forma clara
    assert float(out[:, 2].std()) < float(verts[:, 2].std()) * 0.75
    diag = float(np.linalg.norm(verts.max(axis=0) - verts.min(axis=0)))
    max_move = float(np.linalg.norm(out - verts, axis=1).max())
    assert max_move <= diag * 0.05 + 1e-5


def test_bilateral_denoise_preserves_sharp_90deg_cube() -> None:
    verts, faces = _make_cube()
    out = bilateral_normal_denoise(verts, faces, hard_cutoff_deg=55.0)
    # Todas as faces têm ângulo de 90° entre si (>55°) — os vértices não podem arredondar
    assert np.allclose(out, verts, atol=1e-4)


def test_welded_crease_normals_weld_uv_seams_but_keep_90deg_hard() -> None:
    # Dois triângulos quase coplanares com vértices duplicados na costura UV
    v = np.array(
        [
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            # duplicados na costura (mesmas posições, índices diferentes)
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [1.0, 1.0, 0.15],
        ],
        dtype=np.float32,
    )
    f = np.array([[0, 1, 2], [4, 3, 5]], dtype=np.int32)
    fn = np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]])
    fn /= np.linalg.norm(fn, axis=1, keepdims=True)
    cn = compute_welded_crease_normals(v, f, fn, crease_deg=55.0)
    assert cn.shape == (2, 3, 3)
    # A costura partilhada (v1/v3) deve ter normais idênticas (soldadas)
    assert np.allclose(cn[0, 1], cn[1, 1], atol=1e-5)

    # Cubo 90°: normais por face permanecem ortogonais (não suavizam cantos de 90°)
    vc, fc = _make_cube()
    fnc = np.cross(vc[fc[:, 1]] - vc[fc[:, 0]], vc[fc[:, 2]] - vc[fc[:, 0]])
    fnc /= np.linalg.norm(fnc, axis=1, keepdims=True)
    cnc = compute_welded_crease_normals(vc, fc, fnc, crease_deg=55.0)
    for fi in range(len(fc)):
        for k in range(3):
            assert float(np.dot(cnc[fi, k], fnc[fi])) > 0.999


def test_face_connected_components_sorted_largest_first() -> None:
    v1, f1 = _make_cube()
    # Segundo componente pequeno (2 triângulos) deslocado
    v2 = np.array([[5, 0, 0], [6, 0, 0], [5, 1, 0], [6, 1, 0]], dtype=np.float32)
    f2 = np.array([[0, 1, 2], [1, 3, 2]], dtype=np.int32) + len(v1)
    groups = _face_connected_components(len(v1) + len(v2), np.vstack([f1, f2]))
    assert [len(g) for g in groups] == [12, 2]


def test_streaming_bake_weighted_average_and_boundary_direction() -> None:
    class _Cfg:
        bake_exp = 2

    class _FakeRender:
        def __init__(self) -> None:
            self.calls = 0

        def back_project(self, view, elev, azim):
            self.calls += 1
            h, w = 8, 8
            color_val = float(view)
            tex = torch.full((h, w, 3), color_val, dtype=torch.float32)
            cos = torch.ones((h, w, 1), dtype=torch.float32)
            # Vista 0 tem interior limpo (boundary_map=1.0), Vista 1 é borda (0.0)
            bm = (
                torch.ones((h, w, 1), dtype=torch.float32) if elev == 0 else torch.zeros((h, w, 1), dtype=torch.float32)
            )
            return tex, cos, bm

    vp = ViewProcessor(_Cfg(), _FakeRender())
    tex, valid = vp.bake_from_multiview([1.0, 0.0], [0, 1], [0, 90], [1.0, 1.0])
    assert bool(valid.all())
    # Interior (soft_factor=1.0) deve pesar 4x mais que borda (soft_factor=0.25) -> 1/(1+0.25) = 0.8
    assert float(tex[4, 4, 0].item()) == pytest.approx(0.8, abs=1e-4)


def test_view_selection_8v_symmetric_and_skips_bottom_by_default(monkeypatch) -> None:
    monkeypatch.delenv("PAINT3D_ALLOW_BOTTOM_VIEW", raising=False)

    class _Render:
        default_resolution = 1024

        def set_default_render_resolution(self, r):
            self.default_resolution = r

        def get_face_areas(self, from_one_index=True):
            return np.ones(32, dtype=np.float32)

        def set_boundary_unreliable_scale(self, s):
            pass

        def render_alpha(self, elev, azim, return_type="np"):
            arr = np.zeros((1, 8, 8, 1), dtype=np.int32)
            # Dá área enorme ao elev=-90 para garantir que o filtro a ignora
            if elev <= -80:
                arr[0, :, :, 0] = np.arange(1, 65).reshape(8, 8) % 31 + 1
            else:
                arr[0, 2:6, 2:6, 0] = (abs(int(azim)) % 15) + 1
            return arr

    vp = ViewProcessor(None, _Render())
    elevs = [0, 0, 0, 0, 90, -90, 20, 20, 20, 20]
    azims = [0, 90, 180, 270, 0, 180, 45, 135, 225, 315]
    weights = [1.0, 0.9, 0.9, 0.9, 0.35, 0.05, 0.85, 0.85, 0.85, 0.85]
    sel_e, sel_a, _sel_w = vp.bake_view_selection(elevs, azims, weights, 8)
    assert len(sel_e) == 8
    assert -90 not in sel_e
    assert set(zip(sel_e, sel_a, strict=True)) == {
        (0, 0),
        (0, 90),
        (0, 180),
        (0, 270),
        (20, 45),
        (20, 135),
        (20, 225),
        (20, 315),
    }


def test_sdnq_opt_in_only(monkeypatch) -> None:
    monkeypatch.delenv("PAINT3D_USE_SDNQ", raising=False)
    assert _want_sdnq_quant(True) is False
    assert _want_sdnq_quant(False) is False
    monkeypatch.setenv("PAINT3D_USE_SDNQ", "1")
    assert _want_sdnq_quant(True) is True
    assert _want_sdnq_quant(False) is False


def test_build_scheduler_defaults_to_euler_a(monkeypatch) -> None:
    monkeypatch.delenv("PAINT3D_SCHEDULER", raising=False)
    from diffusers import DDIMScheduler, EulerAncestralDiscreteScheduler, UniPCMultistepScheduler

    base_cfg = UniPCMultistepScheduler().config
    assert isinstance(_build_scheduler(base_cfg), EulerAncestralDiscreteScheduler)
    assert isinstance(_build_scheduler(base_cfg, "unipc"), UniPCMultistepScheduler)
    assert isinstance(_build_scheduler(base_cfg, "ddim"), DDIMScheduler)
