"""Testes para texture2d.image_processor.

``image_processor.save_image`` é um thin wrapper sobre
``aigamekit_shared.image_utils.save_image_with_metadata`` com o formato PNG e o
default ``outputs/textures``. Estes testes cobrem a gravação da imagem, o
sidecar JSON (chaves exactas), o merge de metadata extra, idempotência e o
tratamento de imagens não-RGB — tudo em disco temporário (``tmp_path``).
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from texture2d.image_processor import DEFAULT_OUTPUT_DIR, save_image


def _make_image(mode: str = "RGB", size: tuple[int, int] = (64, 64)) -> Image.Image:
    """Cria uma imagem de teste no modo/tamanho pedidos."""
    color = {"L": 128, "RGBA": (10, 20, 30, 255)}.get(mode, (10, 20, 30))
    return Image.new(mode, size, color=color)


class TestSaveImageBasic:
    def test_writes_png_and_json_sidecar(self, tmp_path: Path):
        image = _make_image()
        params = {"seed": 42, "steps": 28, "width": 1024, "height": 1024, "model_id": "org/model"}
        result = save_image(image, prompt="stone wall", params=params, output_dir=tmp_path, filename="tex.png")

        assert result == tmp_path / "tex.png"
        assert result.exists()
        sidecar = result.with_suffix(".json")
        assert sidecar.exists()

    def test_json_contains_exact_top_level_keys(self, tmp_path: Path):
        result = save_image(_make_image(), prompt="brick", params={"seed": 1}, output_dir=tmp_path, filename="b.png")
        meta = json.loads(result.with_suffix(".json").read_text(encoding="utf-8"))
        assert set(meta.keys()) >= {"timestamp", "prompt", "params", "image_path", "filename"}
        assert meta["prompt"] == "brick"
        assert meta["filename"] == "b.png"
        assert meta["image_path"] == str(result)

    def test_params_nested_in_json(self, tmp_path: Path):
        params = {"seed": 7, "steps": 14, "width": 512, "height": 512, "model_id": "m"}
        result = save_image(_make_image(), prompt="p", params=params, output_dir=tmp_path, filename="n.png")
        meta = json.loads(result.with_suffix(".json").read_text(encoding="utf-8"))
        assert meta["params"] == params

    def test_default_filename_is_timestamped_png(self, tmp_path: Path):
        result = save_image(_make_image(), prompt="p", params={}, output_dir=tmp_path)
        assert result.suffix == ".png"
        assert result.parent == tmp_path
        assert result.with_suffix(".json").exists()


class TestMetadataMerge:
    def test_extra_keys_merged_at_top_level(self, tmp_path: Path):
        result = save_image(
            _make_image(),
            prompt="stone",
            params={"seed": 9},
            output_dir=tmp_path,
            filename="s.png",
            metadata={"prompt_final": "seamless stone", "model_id": "org/model"},
        )
        meta = json.loads(result.with_suffix(".json").read_text(encoding="utf-8"))
        assert meta["prompt_final"] == "seamless stone"
        assert meta["model_id"] == "org/model"
        assert meta["prompt"] == "stone"

    def test_metadata_overrides_default_keys(self, tmp_path: Path):
        result = save_image(
            _make_image(),
            prompt="orig",
            params={},
            output_dir=tmp_path,
            filename="o.png",
            metadata={"prompt": "overridden"},
        )
        meta = json.loads(result.with_suffix(".json").read_text(encoding="utf-8"))
        assert meta["prompt"] == "overridden"


class TestIdempotency:
    def test_double_save_same_filename_overwrites_cleanly(self, tmp_path: Path):
        name = "dup.png"
        save_image(_make_image(), prompt="first", params={"seed": 1}, output_dir=tmp_path, filename=name)
        result = save_image(_make_image(), prompt="second", params={"seed": 2}, output_dir=tmp_path, filename=name)
        meta = json.loads(result.with_suffix(".json").read_text(encoding="utf-8"))
        assert meta["prompt"] == "second"
        assert meta["params"]["seed"] == 2

    def test_two_distinct_files_are_independent(self, tmp_path: Path):
        r1 = save_image(_make_image(), prompt="a", params={"seed": 1}, output_dir=tmp_path, filename="a.png")
        r2 = save_image(_make_image(), prompt="b", params={"seed": 2}, output_dir=tmp_path, filename="b.png")
        m1 = json.loads(r1.with_suffix(".json").read_text(encoding="utf-8"))
        m2 = json.loads(r2.with_suffix(".json").read_text(encoding="utf-8"))
        assert m1["prompt"] == "a" and m2["prompt"] == "b"
        assert m1["image_path"] != m2["image_path"]


class TestNonStandardModes:
    def test_rgba_image_saves(self, tmp_path: Path):
        result = save_image(_make_image("RGBA"), prompt="rgba", params={}, output_dir=tmp_path, filename="rgba.png")
        assert result.exists()

    def test_grayscale_image_saves(self, tmp_path: Path):
        result = save_image(_make_image("L"), prompt="gray", params={}, output_dir=tmp_path, filename="g.png")
        assert result.exists()


class TestOutputDir:
    def test_creates_nested_missing_dirs(self, tmp_path: Path):
        nested = tmp_path / "deep" / "nested" / "out"
        assert not nested.exists()
        result = save_image(_make_image(), prompt="p", params={}, output_dir=nested, filename="x.png")
        assert result.exists()
        assert nested.is_dir()

    def test_default_output_dir_constant(self):
        assert Path("outputs") / "textures" == DEFAULT_OUTPUT_DIR
