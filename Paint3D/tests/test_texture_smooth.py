"""Testes da suavização bilateral de textura UV (cv2/PIL) — sem bpy/GPU."""

from __future__ import annotations

import numpy as np
from PIL import Image

from paint3d.texture_smooth import smooth_texture


def _noise_image(size: int = 32) -> Image.Image:
    rng = np.random.default_rng(0)
    arr = rng.integers(0, 256, size=(size, size, 3), dtype=np.uint8)
    return Image.fromarray(arr, mode="RGB")


class TestSmoothTexture:
    def test_returns_pil_image_same_size(self) -> None:
        img = _noise_image(32)
        out = smooth_texture(img, passes=1)
        assert isinstance(out, Image.Image)
        assert out.size == img.size

    def test_zero_passes_is_identity(self) -> None:
        img = _noise_image(24)
        out = smooth_texture(img, passes=0)
        np.testing.assert_array_equal(np.array(out), np.array(img.convert("RGB")))

    def test_positive_passes_preserve_dimensions(self) -> None:
        img = _noise_image(40)
        out = smooth_texture(img, passes=3, diameter=5)
        assert out.size == img.size

    def test_output_mode_is_rgb(self) -> None:
        img = _noise_image(16)
        out = smooth_texture(img, passes=1)
        assert out.mode == "RGB"

    def test_smoke_random_noise(self) -> None:
        img = _noise_image(32)
        out = smooth_texture(img, passes=2, diameter=9, sigma_color=50.0, sigma_space=50.0)
        arr = np.array(out)
        assert arr.shape == (32, 32, 3)
        assert arr.dtype == np.uint8
