"""Testes para texture2d.image_processor (diffuse PNG + sidecar JSON de metadata)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from PIL import Image

from texture2d.image_processor import DEFAULT_OUTPUT_DIR, save_image


@pytest.fixture(autouse=True)
def _silence_logger(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("texture2d.image_processor.logger", MagicMock())


def _sample_params() -> dict[str, Any]:
    return {
        "prompt_final": "seamless mossy stone, tileable",
        "seed": 4242,
        "model": "Arrexel/pattern-diffusion",
        "seamless_method": "late",
        "quant": "none",
        "guidance_scale": 7.5,
        "num_inference_steps": 50,
        "width": 512,
        "height": 512,
    }


class TestSaveImage:
    def test_writes_png_and_json_sidecar(self, tmp_path: Path) -> None:
        img = Image.new("RGB", (64, 64), color="gray")
        out = save_image(img, "mossy stone", _sample_params(), output_dir=tmp_path, filename="tex.png")

        assert out.exists()
        assert out.name == "tex.png"
        sidecar = out.with_suffix(".json")
        assert sidecar.exists()

    def test_sidecar_contains_new_metadata_keys(self, tmp_path: Path) -> None:
        img = Image.new("RGB", (32, 32), color="green")
        params = _sample_params()
        out = save_image(img, "red brick", params, output_dir=tmp_path, filename="brick.png")

        sidecar = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
        for key in ("prompt_final", "seed", "model", "seamless_method", "quant"):
            assert key in sidecar, f"metadata sem chave top-level: {key}"
        assert sidecar["seed"] == 4242
        assert sidecar["model"] == "Arrexel/pattern-diffusion"
        assert sidecar["seamless_method"] == "late"
        assert sidecar["quant"] == "none"
        assert "prompt" in sidecar

    def test_sidecar_params_nested(self, tmp_path: Path) -> None:
        img = Image.new("RGB", (32, 32), color="blue")
        params = _sample_params()
        out = save_image(img, "wood", params, output_dir=tmp_path, filename="wood.png")

        sidecar = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
        assert "params" in sidecar
        assert sidecar["params"]["guidance_scale"] == 7.5

    def test_falls_back_to_prompt_when_prompt_final_missing(self, tmp_path: Path) -> None:
        img = Image.new("RGB", (16, 16), color="red")
        params = {"seed": 1, "model": "m", "seamless_method": "none", "quant": "none"}
        out = save_image(img, "fallback prompt", params, output_dir=tmp_path, filename="fb.png")

        sidecar = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
        assert sidecar["prompt_final"] == "fallback prompt"

    def test_converts_non_rgb_to_rgb(self, tmp_path: Path) -> None:
        img = Image.new("RGBA", (16, 16), color=(1, 2, 3, 255))
        out = save_image(img, "alpha img", _sample_params(), output_dir=tmp_path, filename="alpha.png")

        reloaded = Image.open(out)
        reloaded.load()
        assert reloaded.mode == "RGB"

    def test_default_output_dir_constant(self) -> None:
        assert Path("outputs") / "textures" == DEFAULT_OUTPUT_DIR
