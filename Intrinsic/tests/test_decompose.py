"""Testes CPU-first do intrinsic_worker — sem torch, modelo mockado."""

from __future__ import annotations

import numpy as np
import pytest
from intrinsic_worker.decompose import (
    DecomposeOutputs,
    invert,
    load_image_rgb,
    output_paths,
    run_decompose,
    save_rgb,
    to_u8,
    upscale_to,
)
from PIL import Image


@pytest.fixture()
def tmp_rgb(tmp_path):
    """Imagem RGB 16x16 com gradiente + disco claro."""
    x = np.linspace(0, 1, 16, dtype=np.float32)
    img = np.stack([np.tile(x, (16, 1))] * 3, axis=-1)
    yy, xx = np.mgrid[0:16, 0:16]
    img[(yy - 8) ** 2 + (xx - 8) ** 2 < 9] = [1.0, 1.0, 1.0]
    p = tmp_path / "in.png"
    Image.fromarray(to_u8(img)).save(p)
    return p, img


class TestInvert:
    def test_invert_ones(self):
        out = invert(np.ones((2, 2, 3), dtype=np.float32))
        assert np.allclose(out, 1.0)

    def test_invert_half(self):
        out = invert(np.full((2, 2, 3), 0.5, dtype=np.float32))
        assert np.allclose(out, 2.0)

    def test_invert_zero_clamped(self):
        out = invert(np.zeros((2, 2, 3), dtype=np.float32))
        assert np.all(out <= 1.0 / 1e-4)

    def test_display_shading_roundtrip_shape(self):
        shd_inv = np.full((4, 4, 3), 2.0, dtype=np.float32)
        display = 1.0 - invert(shd_inv)
        assert np.allclose(display, 0.5)


class TestQuantize:
    def test_to_u8_round_nearest(self):
        assert to_u8(np.array([0.5], dtype=np.float32))[0] == 128  # 127.5 → 128

    def test_to_u8_clamps(self):
        out = to_u8(np.array([-0.2, 1.7], dtype=np.float32))
        assert out[0] == 0 and out[1] == 255

    def test_to_u8_shape(self):
        assert to_u8(np.zeros((5, 7, 3), dtype=np.float32)).shape == (5, 7, 3)


class TestSaveLoad:
    def test_save_rgb_creates_parents(self, tmp_path):
        p = tmp_path / "a" / "b" / "c.png"
        save_rgb(np.zeros((4, 4, 3), dtype=np.float32), p)
        assert p.exists()

    def test_load_image_rgb_roundtrip(self, tmp_rgb):
        p, img = tmp_rgb
        loaded = load_image_rgb(p)
        assert loaded.shape == (16, 16, 3)
        assert loaded.dtype == np.float32
        assert loaded.min() >= 0.0 and loaded.max() <= 1.0
        # Tolerância de quantização 8-bit.
        assert np.abs(loaded - img).max() <= 1.0 / 255.0 + 1e-6

    def test_load_image_rgb_drops_alpha(self, tmp_path):
        rgba = np.zeros((4, 4, 4), dtype=np.uint8)
        rgba[..., 3] = 255
        Image.fromarray(rgba).save(tmp_path / "x.png")
        out = load_image_rgb(tmp_path / "x.png")
        assert out.shape == (4, 4, 3)


class TestUpscale:
    def test_noop_when_same_size(self):
        x = np.random.rand(8, 8, 3).astype(np.float32)
        assert upscale_to(x, 8, 8) is x

    def test_upscale_changes_shape(self):
        x = np.zeros((4, 4, 3), dtype=np.float32)
        out = upscale_to(x, 8, 8)
        assert out.shape == (8, 8, 3)

    def test_upscale_flat_stays_flat(self):
        x = np.full((4, 4, 3), 0.5, dtype=np.float32)
        out = upscale_to(x, 16, 16)
        assert np.allclose(out, out.mean(), atol=0.02)


class TestOutputPaths:
    def test_canonical_names(self, tmp_path):
        paths = output_paths("/x/y/photo.png", tmp_path)
        assert paths.albedo == tmp_path / "photo_albedo.png"
        assert paths.shading == tmp_path / "photo_shading.png"
        assert paths.specular == tmp_path / "photo_specular.png"

    def test_stem_fallback(self, tmp_path):
        paths = output_paths("", tmp_path)
        assert paths.albedo.name.startswith("image_")

    def test_dataclass_fields(self):
        p = DecomposeOutputs(albedo=1, shading=2, specular=3)  # type: ignore[arg-type]
        assert (p.albedo, p.shading, p.specular) == (1, 2, 3)


class TestRunDecompose:
    def _fake_pipeline(self, monkeypatch, shape=(8, 8)):
        """Mocka intrinsic.pipeline.run_pipeline — devolve resultados plausíveis."""

        h, w = shape

        def fake_run(models, img, device="cuda"):
            return {
                "hr_alb": np.full((h // 2, w // 2, 3), 0.4, dtype=np.float32),
                "dif_shd": np.full((h // 2, w // 2, 3), 2.0, dtype=np.float32),
                "pos_res": np.full((h // 2, w // 2, 3), 0.1, dtype=np.float32),
                "residual": np.zeros((h // 2, w // 2, 3), dtype=np.float32),
            }

        class FakePipelineMod:
            run_pipeline = staticmethod(fake_run)

        import sys

        monkeypatch.setitem(sys.modules, "intrinsic", FakePipelineMod)
        monkeypatch.setitem(sys.modules, "intrinsic.pipeline", FakePipelineMod)

    def test_writes_three_pngs(self, tmp_path, monkeypatch, tmp_rgb):
        self._fake_pipeline(monkeypatch)
        src, _ = tmp_rgb
        paths = run_decompose({"models": {}}, src, tmp_path)
        assert paths.albedo.exists()
        assert paths.shading.exists()
        assert paths.specular.exists()

    def test_outputs_at_input_resolution(self, tmp_path, monkeypatch, tmp_rgb):
        self._fake_pipeline(monkeypatch)
        src, _ = tmp_rgb  # 16x16
        paths = run_decompose({"models": {}}, src, tmp_path)
        for p in (paths.albedo, paths.shading, paths.specular):
            with Image.open(p) as im:
                assert im.size == (16, 16)

    def test_shading_is_display_referred(self, tmp_path, monkeypatch, tmp_rgb):
        # dif_shd = 2.0 constante → display = 1 - 1/2 = 0.5 → ~128.
        self._fake_pipeline(monkeypatch)
        src, _ = tmp_rgb
        paths = run_decompose({"models": {}}, src, tmp_path)
        with Image.open(paths.shading) as im:
            arr = np.asarray(im)
        assert abs(int(arr.mean()) - 128) <= 1

    def test_albedo_matches_model_output(self, tmp_path, monkeypatch, tmp_rgb):
        self._fake_pipeline(monkeypatch)
        src, _ = tmp_rgb
        paths = run_decompose({"models": {}}, src, tmp_path)
        with Image.open(paths.albedo) as im:
            arr = np.asarray(im)
        assert abs(int(arr.mean()) - 102) <= 1  # 0.4 → 102
