"""Testes para gamedev_shared.image_utils."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from gamedev_shared.image_utils import (
    create_thumbnail,
    create_zip,
    ensure_rgb,
    load_bytes_as_rgb,
    load_image_metadata,
    safe_filename,
    save_image_with_metadata,
)


@pytest.fixture
def rgb_image() -> Image.Image:
    return Image.new("RGB", (64, 64), color=(255, 0, 0))


@pytest.fixture
def rgba_image() -> Image.Image:
    return Image.new("RGBA", (64, 64), color=(0, 255, 0, 128))


@pytest.fixture
def tmp_output_dir(tmp_path: Path) -> Path:
    d = tmp_path / "output"
    d.mkdir()
    return d


class TestEnsureRgb:
    def test_rgb_image_returned_unchanged(self, rgb_image: Image.Image) -> None:
        result = ensure_rgb(rgb_image)
        assert result.mode == "RGB"
        assert result is rgb_image

    def test_rgba_image_converted(self, rgba_image: Image.Image) -> None:
        result = ensure_rgb(rgba_image)
        assert result.mode == "RGB"

    def test_l_image_converted(self) -> None:
        l_img = Image.new("L", (10, 10), color=128)
        result = ensure_rgb(l_img)
        assert result.mode == "RGB"


class TestLoadBytesAsRgb:
    def test_png_bytes(self, rgb_image: Image.Image) -> None:
        buf = io.BytesIO()
        rgb_image.save(buf, "PNG")
        raw = buf.getvalue()

        result = load_bytes_as_rgb(raw)
        assert result.mode == "RGB"
        assert result.size == (64, 64)

    def test_jpeg_bytes(self) -> None:
        img = Image.new("RGB", (32, 32), color=(0, 0, 255))
        buf = io.BytesIO()
        img.save(buf, "JPEG")
        raw = buf.getvalue()

        result = load_bytes_as_rgb(raw)
        assert result.mode == "RGB"


class TestSafeFilename:
    def test_basic_text(self) -> None:
        assert safe_filename("hello world") == "hello_world"

    def test_special_characters(self) -> None:
        result = safe_filename("my/file: test?.png")
        assert "/" not in result
        assert ":" not in result
        assert "?" not in result

    def test_empty_string(self) -> None:
        assert safe_filename("") == ""

    def test_unicode_characters(self) -> None:
        result = safe_filename("café italiano")
        assert isinstance(result, str)

    def test_max_length(self) -> None:
        long_text = "a" * 200
        result = safe_filename(long_text, max_length=50)
        assert len(result) <= 50


class TestCreateThumbnail:
    def test_default_size(self, rgb_image: Image.Image) -> None:
        result = create_thumbnail(rgb_image)
        assert result.size[0] <= 256
        assert result.size[1] <= 256

    def test_custom_size(self, rgb_image: Image.Image) -> None:
        result = create_thumbnail(rgb_image, size=(32, 32))
        assert result.size[0] <= 32
        assert result.size[1] <= 32

    def test_does_not_modify_original(self, rgb_image: Image.Image) -> None:
        original_size = rgb_image.size
        create_thumbnail(rgb_image, size=(16, 16))
        assert rgb_image.size == original_size


class TestCreateZip:
    def test_creates_zip_with_files(self, tmp_path: Path) -> None:
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("aaa")
        f2.write_text("bbb")
        zip_path = tmp_path / "out.zip"

        result = create_zip([f1, f2], zip_path)
        assert result == zip_path
        assert zip_path.exists()

        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            assert "a.txt" in names
            assert "b.txt" in names

    def test_skips_missing_files(self, tmp_path: Path) -> None:
        f1 = tmp_path / "exists.txt"
        f1.write_text("yes")
        missing = tmp_path / "nope.txt"
        zip_path = tmp_path / "out.zip"

        create_zip([f1, missing], zip_path)

        with zipfile.ZipFile(zip_path) as zf:
            assert "exists.txt" in zf.namelist()
            assert "nope.txt" not in zf.namelist()


class TestSaveImageWithMetadata:
    def test_saves_png_and_json(self, rgb_image: Image.Image, tmp_output_dir: Path) -> None:
        path = save_image_with_metadata(
            rgb_image,
            prompt="test prompt",
            params={"seed": 42},
            output_dir=tmp_output_dir,
            filename="test.png",
        )

        assert path.exists()
        assert path.name == "test.png"

        sidecar = path.with_suffix(".json")
        assert sidecar.exists()
        data = json.loads(sidecar.read_text())
        assert data["prompt"] == "test prompt"
        assert data["params"]["seed"] == 42
        assert data["filename"] == "test.png"

    def test_auto_generates_filename(self, rgb_image: Image.Image, tmp_output_dir: Path) -> None:
        path = save_image_with_metadata(
            rgb_image,
            prompt="auto",
            params={},
            output_dir=tmp_output_dir,
        )
        assert path.exists()
        assert path.suffix == ".png"

    def test_extra_metadata_merged(self, rgb_image: Image.Image, tmp_output_dir: Path) -> None:
        path = save_image_with_metadata(
            rgb_image,
            prompt="meta",
            params={},
            output_dir=tmp_output_dir,
            filename="meta.png",
            metadata={"custom_key": "custom_value"},
        )
        data = json.loads(path.with_suffix(".json").read_text())
        assert data["custom_key"] == "custom_value"


class TestLoadImageMetadata:
    def test_loads_existing_sidecar(self, tmp_output_dir: Path) -> None:
        img_path = tmp_output_dir / "test.png"
        img_path.write_bytes(b"fake")

        sidecar = tmp_output_dir / "test.json"
        sidecar.write_text(json.dumps({"prompt": "hello"}))

        result = load_image_metadata(img_path)
        assert result is not None
        assert result["prompt"] == "hello"

    def test_returns_none_when_no_sidecar(self, tmp_output_dir: Path) -> None:
        img_path = tmp_output_dir / "missing.png"
        result = load_image_metadata(img_path)
        assert result is None

    def test_returns_none_on_invalid_json(self, tmp_output_dir: Path) -> None:
        img_path = tmp_output_dir / "bad.png"
        img_path.write_bytes(b"fake")

        sidecar = tmp_output_dir / "bad.json"
        sidecar.write_text("{invalid json")

        result = load_image_metadata(img_path)
        assert result is None
