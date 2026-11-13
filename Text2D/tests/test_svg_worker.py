"""Ramo `task=vectorize` do adapter do worker (backend text2svg)."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from text2d.worker_serve_adapter import Adapter


def _icon(path: Path, size: int = 48) -> None:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((4, 4, size - 4, size - 4), fill=(30, 120, 240, 255))
    img.save(path)


class _FakeStarVector:
    name = "starvector"
    model_id = "fake/starvector"

    def __init__(self) -> None:
        self.model = object()
        self.warmed = False

    def warmup(self) -> None:
        self.warmed = True

    def vectorize(self, image, opts) -> str:
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48">'
            '<path d="M4 4 L44 44" fill="#1e78f0"/></svg>'
        )

    def unload(self) -> None:
        self.model = None


def test_load_vectorize_vtracer_and_generate(tmp_path):
    adapter = Adapter()
    model = adapter.load(task="vectorize", engine="vtracer")
    assert getattr(model, "name", None) == "vtracer"

    source = tmp_path / "icon.png"
    _icon(source)
    output = tmp_path / "icon.svg"
    result = adapter.generate(
        model,
        {
            "image": str(source),
            "output": str(output),
            "style": "flat",
            "colors": 4,
            "meta": True,
        },
    )
    assert result["status"] == "ok"
    assert result["output"] == str(output)
    assert output.is_file()
    assert result["path_count"] > 0
    adapter.unload(model)


def test_generate_vectorize_requires_image_and_output():
    adapter = Adapter()
    model = _FakeStarVector()
    result = adapter.generate(model, {"output": "/tmp/x.svg"})
    assert result["status"] == "error"
    assert "image" in result["error"]


def test_generate_vectorize_with_starvector_engine(tmp_path, monkeypatch):
    adapter = Adapter()
    model = _FakeStarVector()
    source = tmp_path / "icon.png"
    _icon(source)
    output = tmp_path / "icon.svg"

    result = adapter.generate(
        model,
        {
            "image": str(source),
            "output": str(output),
            "engine": "starvector",
            "style": "flat",
            "colors": 8,
            "meta": True,
        },
    )
    assert result["status"] == "ok"
    assert output.is_file()
    assert result["svg"] == str(output)
    assert result["path_count"] == 1
