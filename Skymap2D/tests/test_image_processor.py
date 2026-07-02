"""Testes para skymap2d.image_processor e correcção equirectangular.

``image_processor`` grava PNG/EXR + sidecar JSON e cria thumbnails 2:1. A
correcção equirectangular documentada (resize 2:1 + shift vertical de 50% para
mover os polos do centro para as bordas) vive em ``generator._fix_equirect_latitude``
e no pós-processamento de ``generate``; é testada aqui por ser a feature crítica
deste módulo.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy
import pytest
from PIL import Image

from skymap2d.generator import SkymapGenerator, _fix_equirect_latitude
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


class TestFixEquirectLatitude:
    def _half_half(self, w: int, h: int) -> Image.Image:
        arr = numpy.zeros((h, w, 3), dtype=numpy.uint8)
        arr[: h // 2] = (255, 0, 0)
        arr[h // 2 :] = (0, 0, 255)
        return Image.fromarray(arr, "RGB")

    def test_even_height_swaps_halves(self):
        img = self._half_half(64, 128)
        out = _fix_equirect_latitude(img)
        out_arr = numpy.asarray(out)
        assert tuple(out_arr[10, 10]) == (0, 0, 255)
        assert tuple(out_arr[118, 10]) == (255, 0, 0)

    def test_dimensions_unchanged(self):
        img = self._half_half(80, 40)
        out = _fix_equirect_latitude(img)
        assert out.size == (80, 40)

    def test_tiny_height_returns_same_object(self):
        img = Image.new("RGB", (32, 2))
        out = _fix_equirect_latitude(img)
        assert out is img

    def test_equivalent_to_numpy_roll_half(self):
        rng = numpy.random.default_rng(0)
        arr = rng.integers(0, 256, size=(64, 48, 3), dtype=numpy.uint8)
        out = _fix_equirect_latitude(Image.fromarray(arr, "RGB"))
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

    def test_shift_applied_after_resize(self):
        gen = SkymapGenerator(device="cpu")
        with patch.object(gen, "_load_pipeline") as mock_load:
            mock_pipe = MagicMock()
            mock_out = MagicMock()
            mock_out.images = [self._half_half(1024, 768)]
            mock_pipe.return_value = mock_out
            mock_load.return_value = mock_pipe

            image, _metadata = gen.generate(prompt="sky", width=2048, height=1024, num_inference_steps=10)

        out_arr = numpy.asarray(image)
        top_region = out_arr[50:150].reshape(-1, 3).mean(axis=0)
        bottom_region = out_arr[900:1000].reshape(-1, 3).mean(axis=0)
        assert top_region[2] > top_region[0]
        assert bottom_region[0] > bottom_region[2]


class TestOutputDir:
    def test_default_output_dir_constant(self):
        assert Path("outputs") / "skymaps" == DEFAULT_OUTPUT_DIR
