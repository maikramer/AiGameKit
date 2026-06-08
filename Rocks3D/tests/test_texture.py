"""Tests for rocks3d.texture — albedo and PBR texture generation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import numpy as np
from rocks3d.defaults import BOULDER, PEBBLE
from rocks3d.texture import (
    _generate_fallback_normal_map,
    _parse_hex_color,
    generate_albedo_texture,
    generate_pbr_textures,
    generate_pbr_with_materialize,
)


class TestParseHexColor:
    def test_parses_hex_with_hash(self) -> None:
        result = _parse_hex_color("#7A7A6F")
        np.testing.assert_array_equal(result, [122, 122, 111])

    def test_parses_hex_without_hash(self) -> None:
        result = _parse_hex_color("5A5A4F")
        np.testing.assert_array_equal(result, [90, 90, 79])

    def test_output_dtype(self) -> None:
        result = _parse_hex_color("#000000")
        assert result.dtype == np.uint8


class TestAlbedoShapeAndRange:
    def test_output_shape(self) -> None:
        albedo = generate_albedo_texture(None, PEBBLE, seed=0, resolution=64)
        assert albedo.shape == (64, 64, 3)

    def test_output_dtype(self) -> None:
        albedo = generate_albedo_texture(None, PEBBLE, seed=0, resolution=64)
        assert albedo.dtype == np.uint8

    def test_values_in_range(self) -> None:
        albedo = generate_albedo_texture(None, PEBBLE, seed=0, resolution=64)
        assert albedo.min() >= 0
        assert albedo.max() <= 255

    def test_boulder_preset(self) -> None:
        albedo = generate_albedo_texture(None, BOULDER, seed=0, resolution=64)
        assert albedo.shape == (64, 64, 3)
        assert albedo.dtype == np.uint8


class TestAlbedoReproducible:
    def test_same_seed_identical(self) -> None:
        a = generate_albedo_texture(None, PEBBLE, seed=42, resolution=64)
        b = generate_albedo_texture(None, PEBBLE, seed=42, resolution=64)
        np.testing.assert_array_equal(a, b)

    def test_different_seed_different(self) -> None:
        a = generate_albedo_texture(None, PEBBLE, seed=0, resolution=64)
        b = generate_albedo_texture(None, PEBBLE, seed=99, resolution=64)
        assert not np.array_equal(a, b)


class TestPbrTexturesReturnsDict:
    def test_returns_albedo_key(self, tmp_path: Path) -> None:
        with patch("rocks3d.texture.generate_albedo_texture", return_value=np.zeros((64, 64, 3), dtype=np.uint8)):
            result = generate_pbr_textures(None, PEBBLE, seed=0, output_dir=tmp_path)
        assert "albedo" in result
        assert result["albedo"].exists()

    def test_returns_normal_key_fallback(self, tmp_path: Path) -> None:
        with patch("rocks3d.texture.generate_albedo_texture", return_value=np.zeros((64, 64, 3), dtype=np.uint8)):
            result = generate_pbr_textures(None, PEBBLE, seed=0, output_dir=tmp_path)
        assert "normal" in result
        assert result["normal"].exists()

    def test_albedo_is_valid_png(self, tmp_path: Path) -> None:
        with patch("rocks3d.texture.generate_albedo_texture", return_value=np.zeros((64, 64, 3), dtype=np.uint8)):
            result = generate_pbr_textures(None, PEBBLE, seed=0, output_dir=tmp_path)
        from PIL import Image

        img = Image.open(str(result["albedo"]))
        assert img.size == (64, 64)

    def test_default_output_dir(self) -> None:
        with patch("rocks3d.texture.generate_albedo_texture", return_value=np.zeros((64, 64, 3), dtype=np.uint8)):
            result = generate_pbr_textures(None, PEBBLE, seed=0)
        assert "albedo" in result
        assert result["albedo"].exists()


class TestMaterializeNotFoundFallback:
    def test_materialize_not_found_returns_empty(self, tmp_path: Path) -> None:
        albedo_path = tmp_path / "test_albedo.png"
        from PIL import Image

        img = Image.new("RGB", (64, 64), (128, 128, 128))
        img.save(str(albedo_path))

        # Simulate the materialize binary being absent so the function
        # takes its not-found branch regardless of the host environment.
        with patch(
            "gamedev_shared.subprocess_utils.resolve_binary",
            side_effect=FileNotFoundError("materialize"),
        ):
            result = generate_pbr_with_materialize(albedo_path, tmp_path)
            assert result == {}

    def test_import_error_triggers_fallback(self, tmp_path: Path) -> None:
        with (
            patch("rocks3d.texture.generate_albedo_texture", return_value=np.zeros((64, 64, 3), dtype=np.uint8)),
            patch("rocks3d.texture.generate_pbr_with_materialize", return_value={}),
        ):
            result = generate_pbr_textures(None, PEBBLE, seed=0, output_dir=tmp_path)
        assert "normal" in result
        assert result["normal"].exists()

    def test_fallback_normal_map_valid(self, tmp_path: Path) -> None:
        albedo = generate_albedo_texture(None, PEBBLE, seed=0, resolution=64)
        output_path = tmp_path / "fallback_normal.png"
        result_path = _generate_fallback_normal_map(albedo, output_path)
        assert result_path.exists()
        from PIL import Image

        img = Image.open(str(result_path))
        assert img.mode == "RGB"
        assert img.size == (64, 64)
