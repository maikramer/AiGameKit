"""Testes para skymap2d.image_processor e correcção equirectangular.

``image_processor`` grava PNG/EXR + sidecar JSON e cria thumbnails 2:1. A
correcção equirectangular (resize 2:1 + roll 50% *só* se ``poles_center``)
vive em ``generator.classify_equirect_layout`` / ``_fix_equirect_latitude``;
é testada aqui por ser a feature crítica deste módulo.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy
import pytest
from PIL import Image

from skymap2d.generator import (
    SkymapGenerator,
    _fix_equirect_latitude,
    _roll_equirect_latitude_half,
    classify_equirect_layout,
)
from skymap2d.image_processor import DEFAULT_OUTPUT_DIR, create_thumbnail, save_image


def _png(path: Path) -> Path:
    Image.new("RGB", (32, 16), color=(12, 34, 56)).save(path)
    return path


class TestSaveImagePng:
    def test_writes_png_and_json_sidecar(self, tmp_path: Path):
        result = save_image(
            Image.new("RGB", (16, 8)), prompt="sunset", params={"seed": 1}, output_dir=tmp_path, filename="s.png"
        )
        assert result == tmp_path / "s.png"
        assert result.exists()
        assert result.with_suffix(".json").exists()

    def test_json_top_level_keys(self, tmp_path: Path):
        result = save_image(
            Image.new("RGB", (16, 8)), prompt="p", params={"seed": 2}, output_dir=tmp_path, filename="k.png"
        )
        meta = json.loads(result.with_suffix(".json").read_text(encoding="utf-8"))
        assert set(meta.keys()) >= {"timestamp", "prompt", "params", "image_path", "filename"}
        assert meta["params"]["seed"] == 2

    def test_default_png_filename_from_shared(self, tmp_path: Path):
        result = save_image(Image.new("RGB", (16, 8)), prompt="p", params={}, output_dir=tmp_path)
        assert result.suffix == ".png"
        assert result.stem.startswith("image_")

    def test_metadata_merge(self, tmp_path: Path):
        result = save_image(
            Image.new("RGB", (16, 8)),
            prompt="p",
            params={},
            output_dir=tmp_path,
            filename="m.png",
            metadata={"prompt_final": "equirect p", "model_id": "org/m"},
        )
        meta = json.loads(result.with_suffix(".json").read_text(encoding="utf-8"))
        assert meta["prompt_final"] == "equirect p"
        assert meta["model_id"] == "org/m"


class TestSaveImageExr:
    def _read_exr_max(self, path: Path) -> float:
        import OpenEXR

        arr = OpenEXR.File(str(path)).channels()["RGB"].pixels
        return float(numpy.asarray(arr).max())

    def test_writes_exr_and_json_sidecar(self, tmp_path: Path):
        pytest.importorskip("OpenEXR")
        result = save_image(
            Image.new("RGB", (16, 8), color=(255, 255, 255)),
            prompt="bright",
            params={"seed": 3},
            output_dir=tmp_path,
            filename="sky.exr",
            image_format="exr",
        )
        assert result == tmp_path / "sky.exr"
        assert result.exists()
        meta = json.loads(result.with_suffix(".json").read_text(encoding="utf-8"))
        assert meta["image_format"] == "exr"
        assert meta["color_space"] == "linear_rgb"

    def test_default_exr_filename(self, tmp_path: Path):
        pytest.importorskip("OpenEXR")
        result = save_image(
            Image.new("RGB", (16, 8)),
            prompt="p",
            params={},
            output_dir=tmp_path,
            image_format="exr",
        )
        assert result.suffix == ".exr"
        assert result.stem.startswith("skymap_")

    def test_exr_scale_doubles_linear_values(self, tmp_path: Path):
        pytest.importorskip("OpenEXR")
        base = save_image(
            Image.new("RGB", (16, 8), color=(255, 255, 255)),
            prompt="p",
            params={},
            output_dir=tmp_path,
            filename="a.exr",
            image_format="exr",
            exr_scale=1.0,
        )
        scaled = save_image(
            Image.new("RGB", (16, 8), color=(255, 255, 255)),
            prompt="p",
            params={},
            output_dir=tmp_path,
            filename="b.exr",
            image_format="exr",
            exr_scale=2.0,
        )
        assert self._read_exr_max(scaled) == pytest.approx(2.0 * self._read_exr_max(base))


class TestSaveImageInvalidFormat:
    def test_rejects_unsupported_format(self, tmp_path: Path):
        with pytest.raises(ValueError, match="png ou exr"):
            save_image(
                Image.new("RGB", (16, 8)),
                prompt="p",
                params={},
                output_dir=tmp_path,
                filename="x.jpg",
                image_format="jpg",
            )


class TestCreateThumbnail:
    def test_default_is_2_to_1(self):
        thumb = create_thumbnail(Image.new("RGB", (4096, 2048)))
        w, h = thumb.size
        assert w <= 512 and h <= 256
        assert w == 2 * h

    def test_custom_size(self):
        thumb = create_thumbnail(Image.new("RGB", (1000, 500)), size=(200, 100))
        assert thumb.size == (200, 100)


def _poles_center_image(w: int = 128, h: int = 64) -> Image.Image:
    """Horizon detail on top/bottom bands, smooth pole band in the middle."""
    arr = numpy.zeros((h, w, 3), dtype=numpy.uint8)
    arr[:] = (40, 80, 160)  # smooth mid (poles)
    rng = numpy.random.default_rng(1)
    band = max(2, h // 8)
    noise = rng.integers(0, 256, size=(band, w, 3), dtype=numpy.uint8)
    arr[:band] = noise
    arr[-band:] = rng.integers(0, 256, size=(band, w, 3), dtype=numpy.uint8)
    return Image.fromarray(arr, "RGB")


def _correct_equirect_image(w: int = 128, h: int = 64) -> Image.Image:
    """Horizon detail in the middle band, smooth poles at the edges."""
    arr = numpy.zeros((h, w, 3), dtype=numpy.uint8)
    arr[:] = (30, 60, 140)
    rng = numpy.random.default_rng(2)
    mid0, mid1 = h * 3 // 8, h * 5 // 8
    arr[mid0:mid1] = rng.integers(0, 256, size=(mid1 - mid0, w, 3), dtype=numpy.uint8)
    return Image.fromarray(arr, "RGB")


def _little_planet_image(w: int = 128, h: int = 64) -> Image.Image:
    """Noisy disk at centre, flat outer ring."""
    arr = numpy.full((h, w, 3), 200, dtype=numpy.uint8)
    rng = numpy.random.default_rng(3)
    yy, xx = numpy.mgrid[0:h, 0:w]
    cy, cx = (h - 1) * 0.5, (w - 1) * 0.5
    r = numpy.sqrt(((yy - cy) / (h * 0.5)) ** 2 + ((xx - cx) / (w * 0.5)) ** 2)
    disk = r < 0.35
    arr[disk] = rng.integers(0, 256, size=(int(disk.sum()), 3), dtype=numpy.uint8)
    return Image.fromarray(arr, "RGB")


class TestClassifyEquirectLayout:
    def test_correct(self):
        assert classify_equirect_layout(_correct_equirect_image()) == "correct"

    def test_poles_center(self):
        assert classify_equirect_layout(_poles_center_image()) == "poles_center"

    def test_little_planet(self):
        assert classify_equirect_layout(_little_planet_image()) == "little_planet"


class TestFixEquirectLatitude:
    def test_poles_center_rolls(self):
        img = _poles_center_image(64, 128)
        out = _fix_equirect_latitude(img)
        expected = numpy.asarray(_roll_equirect_latitude_half(img))
        numpy.testing.assert_array_equal(numpy.asarray(out), expected)

    def test_correct_unchanged(self):
        img = _correct_equirect_image(80, 40)
        out = _fix_equirect_latitude(img)
        numpy.testing.assert_array_equal(numpy.asarray(out), numpy.asarray(img))

    def test_little_planet_unchanged(self):
        img = _little_planet_image(96, 48)
        out = _fix_equirect_latitude(img)
        numpy.testing.assert_array_equal(numpy.asarray(out), numpy.asarray(img))

    def test_tiny_height_returns_same_object(self):
        img = Image.new("RGB", (32, 2))
        out = _fix_equirect_latitude(img)
        assert out is img

    def test_roll_helper_equivalent_to_numpy_roll_half(self):
        rng = numpy.random.default_rng(0)
        arr = rng.integers(0, 256, size=(64, 48, 3), dtype=numpy.uint8)
        out = _roll_equirect_latitude_half(Image.fromarray(arr, "RGB"))
        expected = numpy.roll(arr, arr.shape[0] // 2, axis=0)
        numpy.testing.assert_array_equal(numpy.asarray(out), expected)


class TestGenerateEquirectCorrection:
    def _half_half(self, w: int, h: int) -> Image.Image:
        arr = numpy.zeros((h, w, 3), dtype=numpy.uint8)
        arr[: h // 2] = (255, 0, 0)
        arr[h // 2 :] = (0, 0, 255)
        return Image.fromarray(arr, "RGB")

    def test_wrong_ratio_resized_to_2to1(self):
        gen = SkymapGenerator(device="cpu")
        with patch.object(gen, "_load_pipeline") as mock_load:
            mock_pipe = MagicMock()
            mock_out = MagicMock()
            mock_out.images = [self._half_half(1024, 768)]
            mock_pipe.return_value = mock_out
            mock_load.return_value = mock_pipe

            image, _metadata = gen.generate(prompt="sky", width=2048, height=1024, num_inference_steps=10)

        assert image.size == (2048, 1024)

    def test_shift_applied_after_resize_when_poles_center(self):
        """Wrong ratio → resize 2:1, then roll only if still poles_center."""
        gen = SkymapGenerator(device="cpu")
        src = _poles_center_image(1024, 768)
        with patch.object(gen, "_load_pipeline") as mock_load:
            mock_pipe = MagicMock()
            mock_out = MagicMock()
            mock_out.images = [src]
            mock_pipe.return_value = mock_out
            mock_load.return_value = mock_pipe

            image, _metadata = gen.generate(prompt="sky", width=2048, height=1024, num_inference_steps=10)

        assert image.size == (2048, 1024)
        resized = src.resize((2048, 1024), Image.Resampling.LANCZOS)
        expected = _roll_equirect_latitude_half(resized)
        numpy.testing.assert_allclose(
            numpy.asarray(image, dtype=numpy.float32),
            numpy.asarray(expected, dtype=numpy.float32),
            atol=2.0,
        )


class TestOutputDir:
    def test_default_output_dir_constant(self):
        assert Path("outputs") / "skymaps" == DEFAULT_OUTPUT_DIR
