"""Tests for Paint3D kernel, SDNQ safety, and bpy vectorization optimizations."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest
import torch
from PIL import Image

from paint3d.painter import (
    PAINT_UNET_QUANT_SKIP_MODULES,
    _apply_translation,
    _apply_uniform_scale,
    _quantize_paint_unet,
)
from paint3d.texture_smooth import _bpy_image_to_pil as smooth_to_pil
from paint3d.texture_smooth import _write_pil_to_bpy as smooth_write_pil
from paint3d.texture_upscale import _bpy_image_to_pil as up_to_pil
from paint3d.texture_upscale import _tiled_inference
from paint3d.texture_upscale import _write_pil_to_bpy as up_write_pil


class _FakeVerts:
    def __init__(self, pts: np.ndarray) -> None:
        self._pts = np.asarray(pts, dtype=np.float64).copy()

    def __len__(self) -> int:
        return len(self._pts)

    def foreach_get(self, attr: str, buf: np.ndarray) -> None:
        assert attr == "co"
        buf[:] = self._pts.ravel()

    def foreach_set(self, attr: str, buf: np.ndarray) -> None:
        assert attr == "co"
        self._pts[:] = np.asarray(buf, dtype=np.float64).reshape(-1, 3)


class _FakePixels:
    def __init__(self, arr: np.ndarray) -> None:
        self._arr = np.asarray(arr, dtype=np.float32).ravel().copy()

    def foreach_get(self, buf: np.ndarray) -> None:
        buf[:] = self._arr

    def foreach_set(self, buf: np.ndarray) -> None:
        self._arr[:] = np.asarray(buf, dtype=np.float32).ravel()


class _FakeBpyImage:
    def __init__(self, w: int, h: int, arr: np.ndarray) -> None:
        self.size = [w, h]
        self.pixels = _FakePixels(arr)
        self.updated = False
        self.packed = False

    def scale(self, w: int, h: int) -> None:
        self.size = [w, h]
        self.pixels = _FakePixels(np.zeros((h, w, 4), dtype=np.float32))

    def update(self) -> None:
        self.updated = True

    def pack(self) -> None:
        self.packed = True


class TestQuantizePaintUnet:
    def test_passes_skip_modules_and_cpu_return_when_go_engages(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        def _fake_quant(model: Any, **kwargs: Any) -> Any:
            captured.update(kwargs)
            return model

        monkeypatch.setattr("aigamekit_shared.sdnq.quantize_model", _fake_quant)
        monkeypatch.setattr("paint3d.painter.POLICY", SimpleNamespace(will_engage=lambda allow=True: True))
        monkeypatch.setattr("paint3d.painter.clear_cuda_memory", lambda: None)

        pipe = SimpleNamespace(unet=MagicMock())
        _quantize_paint_unet(pipe, allow_group_offload=True, verbose=False)

        assert captured["preset"] == "sdnq-uint8"
        assert captured["dequantize_fp32"] is False
        assert captured["return_device"] == "cpu"
        assert captured["modules_to_not_convert"] == list(PAINT_UNET_QUANT_SKIP_MODULES)
        for mod in ("conv_in", "conv_out", "time_emb", "image_proj_model_dino", "learned_text_clip"):
            assert mod in captured["modules_to_not_convert"]

    def test_raises_runtime_error_on_partial_quant_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _boom(*args: Any, **kwargs: Any) -> Any:
            raise ValueError("simulated failure")

        monkeypatch.setattr("aigamekit_shared.sdnq.quantize_model", _boom)
        monkeypatch.setattr("paint3d.painter.POLICY", SimpleNamespace(will_engage=lambda allow=True: False))

        pipe = SimpleNamespace(unet=MagicMock())
        with pytest.raises(RuntimeError, match="Quantização SDNQ do UNet falhou"):
            _quantize_paint_unet(pipe, allow_group_offload=False)


class TestBpyVectorization:
    def test_apply_translation_and_uniform_scale_foreach(self) -> None:
        pts = np.array([[0.0, 0.0, 0.0], [2.0, 4.0, 6.0]], dtype=np.float64)
        verts = _FakeVerts(pts)
        obj = SimpleNamespace(data=SimpleNamespace(vertices=verts, update=MagicMock()))

        _apply_translation([obj], np.array([1.0, -2.0, 3.0]))
        np.testing.assert_allclose(verts._pts, [[1.0, -2.0, 3.0], [3.0, 2.0, 9.0]])

        _apply_uniform_scale([obj], np.array([1.0, -2.0, 3.0]), 0.5)
        np.testing.assert_allclose(verts._pts, [[1.0, -2.0, 3.0], [2.0, 0.0, 6.0]])

    @pytest.mark.parametrize(("to_pil", "write_pil"), [(smooth_to_pil, smooth_write_pil), (up_to_pil, up_write_pil)])
    def test_bpy_image_roundtrip_foreach(self, to_pil: Any, write_pil: Any) -> None:
        rgba = np.zeros((4, 6, 4), dtype=np.float32)
        rgba[:, :, 0] = 1.0
        rgba[:, :, 1] = 0.5
        rgba[:, :, 3] = 1.0
        img_obj = _FakeBpyImage(6, 4, rgba)

        pil = to_pil(img_obj)
        assert pil.size == (6, 4)
        arr = np.asarray(pil)
        assert arr[0, 0, 0] == 255 and abs(int(arr[0, 0, 1]) - 127) <= 1 and arr[0, 0, 2] == 0

        new_pil = Image.new("RGB", (6, 4), (10, 20, 30))
        write_pil(new_pil, img_obj)
        assert img_obj.updated and img_obj.packed
        written = img_obj.pixels._arr.reshape(4, 6, 4)
        np.testing.assert_allclose(written[0, 0], [10 / 255.0, 20 / 255.0, 30 / 255.0, 1.0], atol=1e-5)

    def test_tiled_inference_matches_full_without_weights_buffer(self) -> None:
        class _DoubleScale(torch.nn.Module):
            def forward(self, x: torch.Tensor) -> torch.Tensor:
                return torch.nn.functional.interpolate(x, scale_factor=2, mode="nearest")

        x = torch.randn(1, 3, 10, 12)
        out = _tiled_inference(_DoubleScale(), x, tile_size=4, tile_pad=2, scale=2, verbose=False)
        expected = torch.nn.functional.interpolate(x, scale_factor=2, mode="nearest")
        assert out.shape == expected.shape
        assert torch.allclose(out, expected)


class TestCustomRasterizerInterpolate:
    def test_cpu_fallback_zeros_background_and_interpolates_foreground(self) -> None:
        pytest.importorskip("nvdiffrast.torch")
        from paint3d.custom_rasterizer_shim import render as cr

        col = torch.tensor([[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]])
        tri = torch.tensor([[0, 1, 2]], dtype=torch.int32)
        findices = torch.tensor([[0, 1], [1, 0]], dtype=torch.int32)
        bary = torch.tensor(
            [
                [[1.0, 0.0, 0.0], [0.5, 0.25, 0.25]],
                [[0.0, 0.5, 0.5], [1.0, 0.0, 0.0]],
            ],
            dtype=torch.float32,
        )
        out = cr.interpolate(col, findices, bary, tri)
        assert out.shape == (1, 2, 2, 3)
        assert torch.allclose(out[0, 0, 0], torch.zeros(3))
        assert torch.allclose(out[0, 1, 1], torch.zeros(3))
        assert torch.allclose(out[0, 0, 1], torch.tensor([0.5, 0.25, 0.25]))
        assert torch.allclose(out[0, 1, 0], torch.tensor([0.0, 0.5, 0.5]))

    @pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA required")
    def test_cuda_nvdiffrast_interpolate_parity(self) -> None:
        pytest.importorskip("nvdiffrast.torch")
        from paint3d.custom_rasterizer_shim import render as cr

        pos = torch.tensor(
            [[[-0.8, -0.8, 0.5, 1.0], [0.8, -0.6, 0.5, 1.0], [0.1, 0.9, 0.5, 1.0]]],
            device="cuda",
            dtype=torch.float32,
        )
        tri = torch.tensor([[0, 1, 2]], device="cuda", dtype=torch.int32)
        col = torch.tensor([[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]], device="cuda", dtype=torch.float32)

        findices, bary = cr.rasterize(pos, tri, (128, 128))
        out_gpu = cr.interpolate(col, findices, bary, tri).cpu()
        out_cpu = cr.interpolate(col.cpu(), findices.cpu(), bary.cpu(), tri.cpu())
        assert torch.allclose(out_gpu, out_cpu, atol=1e-5)


class TestPaintPrepKdTreeGutterFix:
    def test_sphere_with_zero_gutter_completes_fast(self) -> None:
        import time

        from paint3d.paint_prep import fill_far_holes

        rng = np.random.default_rng(0)
        h, w = 256, 256
        tex = np.zeros((h, w, 3), dtype=np.float32)
        pos4 = np.zeros((h, w, 4), dtype=np.float32)
        trusted = np.zeros((h, w), dtype=bool)
        far_holes = np.zeros((h, w), dtype=bool)

        # Half of atlas: points on unit sphere (trusted)
        pts = rng.normal(size=(h // 2, w, 3)).astype(np.float32)
        pts /= np.linalg.norm(pts, axis=-1, keepdims=True) + 1e-8
        pos4[: h // 2, :, :3] = pts
        pos4[: h // 2, :, 3] = 1.0
        tex[: h // 2] = np.array([0.8, 0.2, 0.1], dtype=np.float32)
        trusted[: h // 2] = True

        # Remaining half: UV gutter (0,0,0, coverage=0) + 1000 interior sphere points (coverage=1)
        far_holes[h // 2 :] = True
        pos4[h // 2 : h // 2 + 4, :250, :3] = -pts[:4, :250]
        pos4[h // 2 : h // 2 + 4, :250, 3] = 1.0

        t0 = time.perf_counter()
        out = fill_far_holes(tex, far_holes, trusted=trusted, position_map=pos4)
        dt = time.perf_counter() - t0
        assert dt < 2.0, f"fill_far_holes took too long ({dt:.2f}s)"
        np.testing.assert_allclose(out[h // 2 + 10, 10], [0.8, 0.2, 0.1], atol=1e-4)
