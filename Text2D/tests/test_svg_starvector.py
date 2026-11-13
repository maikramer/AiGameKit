"""Contrato do StarVectorEngine (sem pesos) + teste real opt-in."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from text2d.svg.options import SvgOptions
from text2d.svg.starvector import StarVectorEngine, prepare_image

_VAEL_ICON = Path("/home/maikeu/GitClones/Vael/assets/public/assets/icons/ability_dash.png")


def _icon(size: int = 64) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((4, 4, size - 4, size - 4), fill=(30, 120, 240, 255))
    return img


class _FakeModel:
    def __init__(self) -> None:
        self.calls: list[tuple[dict, dict]] = []

    def generate_im2svg(self, batch, **kwargs):
        self.calls.append((batch, kwargs))
        return [
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
            '<path d="M4 4 L60 60" fill="#1e78f0"/></svg>'
        ]


def test_prepare_image_is_clip_ready():
    tensor = prepare_image(_icon(64))
    assert tuple(tensor.shape) == (1, 3, 224, 224)
    assert float(tensor.min()) < 0.0 < float(tensor.max())


def test_engine_calls_generate_im2svg_with_deterministic_kwargs():
    import torch

    engine = StarVectorEngine(device="cpu", max_tokens=123)
    fake = _FakeModel()
    engine.model = fake
    engine.device = "cpu"
    engine.dtype = torch.float32

    svg = engine.vectorize(_icon(64), SvgOptions(engine="starvector", max_tokens=123, seed=7))
    assert svg.startswith("<svg")
    batch, kwargs = fake.calls[0]
    assert tuple(batch["image"].shape) == (1, 3, 224, 224)
    assert kwargs["max_length"] == 123
    assert kwargs["use_nucleus_sampling"] is False
    assert kwargs["num_beams"] == 1
    engine.unload()


@pytest.mark.skipif(
    os.environ.get("TEXT2D_STARVECTOR") != "1",
    reason="teste real do StarVector: define TEXT2D_STARVECTOR=1 (GPU + pesos em cache)",
)
@pytest.mark.skipif(not _VAEL_ICON.is_file(), reason="ícone de QA do Vael ausente")
def test_starvector_real_model_on_vael_icon(tmp_path):
    from text2d.svg.api import vectorize_file
    from text2d.svg.validate import normalize_svg

    output = tmp_path / "ability_dash_starvector.svg"
    result = vectorize_file(
        _VAEL_ICON,
        output,
        SvgOptions(engine="starvector", colors=8, detail="medium", max_tokens=4000, meta=False),
    )
    assert output.is_file()
    assert result.path_count > 0
    svg = output.read_text(encoding="utf-8")
    normalized, _ = normalize_svg(svg, width=512, height=512)
    assert normalized.startswith("<svg")
