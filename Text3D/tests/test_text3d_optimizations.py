"""Unit tests for Text3D performance/quality optimizations and bug fixes."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest
import torch
import trimesh
from PIL import Image

from text3d.generator import HUNYUAN_DIT_QUANT_SKIP_MODULES, HunyuanTextTo3DGenerator
from text3d.utils.bg_removal import BiRefNetBGRemover


class _DummyPipe:
    def __init__(self) -> None:
        self.device = torch.device("cpu")
        self.dtype = torch.float32
        self.last_image: Image.Image | None = None
        self.last_kwargs: dict[str, Any] = {}
        self.image_processor = MagicMock(size=512)
        self.cond_encoder = MagicMock()
        self.vae = MagicMock()
        self.model = MagicMock()
        self.scheduler = MagicMock()

    def __call__(self, **kwargs: Any) -> trimesh.Trimesh:
        self.last_image = kwargs.get("image")
        self.last_kwargs = kwargs
        return trimesh.creation.box()


class TestAlphaPreservationAndBiRefNetOffload:
    def test_rgba_png_preserves_alpha_and_skips_birefnet(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        img = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
        for x in range(8, 24):
            for y in range(8, 24):
                img.putpixel((x, y), (200, 100, 50, 255))
        png = tmp_path / "cutout.png"
        img.save(png)

        called = False

        class _ExplodingRemover:
            def __init__(self, device: str | None = None) -> None:
                nonlocal called
                called = True

            def remove_background(self, image: Image.Image) -> Image.Image:
                raise AssertionError("BiRefNet should be skipped when PNG already has meaningful alpha")

        monkeypatch.setattr("text3d.generator.BiRefNetBGRemover", _ExplodingRemover)
        gen = HunyuanTextTo3DGenerator(device="cpu")
        pipe = _DummyPipe()
        monkeypatch.setattr(gen, "_load_hunyuan", lambda: pipe)

        gen.generate_from_image(png, remove_bg=True, keep_loaded=True)
        assert not called
        assert pipe.last_image is not None
        assert pipe.last_image.mode == "RGBA"
        # Cropped around the 16x16 opaque box (+5% pad), smaller than 32x32
        assert pipe.last_image.width < 32 and pipe.last_image.height < 32

    def test_rgba_png_preserves_alpha_with_remove_bg_false(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        img = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
        for x in range(10, 22):
            for y in range(10, 22):
                img.putpixel((x, y), (80, 120, 200, 255))
        png = tmp_path / "alpha_only.png"
        img.save(png)

        gen = HunyuanTextTo3DGenerator(device="cpu")
        pipe = _DummyPipe()
        monkeypatch.setattr(gen, "_load_hunyuan", lambda: pipe)

        gen.generate_from_image(png, remove_bg=False, keep_loaded=True)
        assert pipe.last_image is not None
        assert pipe.last_image.mode == "RGBA"
        alpha = np.asarray(pipe.last_image.getchannel("A"))
        assert (alpha == 0).any() and (alpha == 255).any()

    def test_keep_loaded_calls_offload_to_cpu_on_birefnet(self, monkeypatch: pytest.MonkeyPatch) -> None:
        events: list[str] = []

        class _TrackingRemover:
            def __init__(self, device: str | None = None) -> None:
                events.append("init")

            def remove_background(self, image: Image.Image) -> Image.Image:
                events.append("remove")
                return image.convert("RGBA")

            def offload_to_cpu(self) -> None:
                events.append("offload")

            def unload(self) -> None:
                events.append("unload")

        monkeypatch.setattr("text3d.generator.BiRefNetBGRemover", _TrackingRemover)
        gen = HunyuanTextTo3DGenerator(device="cpu")
        monkeypatch.setattr(gen, "_load_hunyuan", lambda: _DummyPipe())

        rgb = Image.new("RGB", (16, 16), (255, 255, 255))
        gen.generate_from_image(rgb, remove_bg=True, keep_loaded=True)
        assert events == ["init", "remove", "offload"]
        gen.unload_hunyuan()
        assert events == ["init", "remove", "offload", "unload"]

    def test_birefnet_offload_to_cpu_and_reload_cuda_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        remover = BiRefNetBGRemover(device="cuda:0")
        fake_model = MagicMock()
        fake_model.dtype = torch.bfloat16
        remover._model = fake_model
        remover._on_cpu = False

        monkeypatch.setattr(remover, "_is_cuda_device", lambda: True)
        monkeypatch.setattr("text3d.utils.bg_removal.clear_cuda_memory", lambda: None)

        remover.offload_to_cpu()
        fake_model.to.assert_called_with("cpu")
        assert remover._on_cpu is True

        fake_model.to.reset_mock()
        remover._ensure_loaded()
        fake_model.to.assert_called_with("cuda:0", non_blocking=True)
        assert remover._on_cpu is False


class TestGenerateKwargsAndUnload:
    def test_generate_forwards_dino_and_image_processor_size(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        class _FakeT2D:
            def __init__(self, **kwargs: Any) -> None:
                pass

            def generate(self, **kwargs: Any) -> tuple[Image.Image, dict[str, Any]]:
                return Image.new("RGB", (16, 16), (128, 128, 128)), {}

            def unload(self) -> None:
                pass

        monkeypatch.setattr("text3d.generator.KleinFluxGenerator", _FakeT2D)
        gen = HunyuanTextTo3DGenerator(device="cpu")

        def _fake_from_image(img: Any, **kwargs: Any) -> trimesh.Trimesh:
            captured.update(kwargs)
            return trimesh.creation.box()

        monkeypatch.setattr(gen, "generate_from_image", _fake_from_image)
        gen.generate("a stone sword", optimize_prompt=False, dino_image_size=560, image_processor_size=560)
        assert captured["dino_image_size"] == 560
        assert captured["image_processor_size"] == 560

    def test_unload_hunyuan_moves_cond_encoder_and_vae_to_cpu(self) -> None:
        gen = HunyuanTextTo3DGenerator(device="cpu")
        pipe = _DummyPipe()
        model_ref, cond_ref, vae_ref = pipe.model, pipe.cond_encoder, pipe.vae
        gen._hunyuan_pipeline = pipe

        gen._unload_hunyuan()
        model_ref.to.assert_called_with("cpu")
        cond_ref.to.assert_called_with("cpu")
        vae_ref.to.assert_called_with("cpu")
        assert pipe.model is None and pipe.cond_encoder is None and pipe.vae is None
        assert gen._hunyuan_pipeline is None

    def test_hunyuan_quant_skip_modules_contains_sensitive_layers(self) -> None:
        for expected in ("x_embedder", "t_embedder", "final_layer", "moe.gate", "default_modulation"):
            assert expected in HUNYUAN_DIT_QUANT_SKIP_MODULES


class TestMoEInferAndPreloadedSubmodules:
    def test_moe_infer_index_add_matches_scatter_reduce(self) -> None:
        from text3d.hy3dshape_paths import ensure_hy3dshape_on_path

        ensure_hy3dshape_on_path(quiet=True)
        from hy3dshape.models.denoisers.moe_layers import MoEBlock

        torch.manual_seed(123)
        block = MoEBlock(dim=32, num_experts=4, moe_top_k=2).eval()
        x = torch.randn(10, 32)
        flat_idx = torch.randint(0, 4, (20,))
        flat_w = torch.rand(20, 1)

        out = block.moe_infer(x, flat_idx, flat_w)

        # Reference implementation using original scatter_reduce_
        ref = torch.zeros_like(x)
        idxs = flat_idx.argsort()
        tpe = flat_idx.bincount().cpu().numpy().cumsum(0)
        token_idxs = idxs // block.moe_top_k
        with torch.no_grad():
            for i, end_idx in enumerate(tpe):
                start_idx = 0 if i == 0 else tpe[i - 1]
                if start_idx == end_idx:
                    continue
                exp_token_idx = token_idxs[start_idx:end_idx]
                exp_out = block.experts[i](x[exp_token_idx])
                exp_out.mul_(flat_w[idxs[start_idx:end_idx]])
                ref.scatter_reduce_(0, exp_token_idx.view(-1, 1).repeat(1, x.shape[-1]), exp_out, reduce="sum")

        assert torch.allclose(out, ref, atol=1e-6)
