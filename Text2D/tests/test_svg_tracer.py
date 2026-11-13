"""VTracer: preparação, tracing, remoção de fundo e escrita."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from text2d.svg.api import save_svg, vectorize_file
from text2d.svg.meta import meta_path_for
from text2d.svg.options import SvgOptions
from text2d.svg.tracer import (
    VtracerEngine,
    prepare_raster,
    strip_background_paths,
    trace_image,
)
from text2d.svg.validate import normalize_svg


def _icon(size: int = 64) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((6, 6, size - 6, size - 6), fill=(220, 40, 40, 255))
    draw.rectangle((size // 2 - 4, 16, size // 2 + 4, size - 16), fill=(250, 210, 40, 255))
    return img


def test_prepare_raster_uses_a_sentinel_absent_from_the_palette():
    opts = SvgOptions(engine="vtracer", colors=4, transparent=True)
    prepared, background, require_canvas = prepare_raster(_icon(), opts)
    assert prepared.mode == "RGB"
    assert background is not None
    assert require_canvas is False
    # O fundo sentinela não deve estar perto das cores do ícone.
    colors = [c for _, c in (prepared.getcolors(maxcolors=1 << 16) or [])]
    assert any(c == background for c in colors)


def test_trace_image_is_valid_and_deterministic():
    opts = SvgOptions(engine="vtracer", colors=4, detail="low", transparent=True)
    icon = _icon()
    first = trace_image(icon, opts)
    second = trace_image(icon, opts)
    assert first == second
    assert "<svg" in first
    assert "path" in first
    svg, paths = normalize_svg(first, width=64, height=64)
    assert paths > 0
    assert "#fd00fe" not in svg.lower()
    assert "#ff00ff" not in svg.lower()


def test_strip_background_paths_removes_sentinel_only():
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
        '<path d="M0 0 C10 0 10 10 0 10 Z" fill="#FD00FE"/>'
        '<path d="M1 1 L9 9" fill="#123456"/>'
        "</svg>"
    )
    stripped = strip_background_paths(
        svg, (255, 0, 255), canvas=(10, 10), require_canvas=False
    )
    assert "#123456" in stripped
    assert "#FD00FE" not in stripped


def test_vtracer_engine_contract():
    engine = VtracerEngine()
    engine.warmup()
    svg = engine.vectorize(_icon(48), SvgOptions(engine="vtracer", colors=4, detail="low"))
    assert "<svg" in svg
    engine.unload()


def test_empty_svg_warns_or_raises_in_strict(tmp_path: Path):
    import pytest

    from text2d.svg.validate import SvgValidationError

    empty = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 8 8"></svg>'
    result = save_svg(empty, tmp_path / "empty.svg", SvgOptions(engine="vtracer", strict=False, meta=False))
    assert result.path_count == 0
    assert "sem paths" in (result.warning or "")

    with pytest.raises(SvgValidationError):
        save_svg(
            empty,
            tmp_path / "empty_strict.svg",
            SvgOptions(engine="vtracer", strict=True, meta=False),
        )


def test_vectorize_file_writes_svg_and_meta(tmp_path: Path):
    source = tmp_path / "icon.png"
    _icon(48).save(source)
    output = tmp_path / "icon.svg"
    result = vectorize_file(
        source,
        output,
        SvgOptions(engine="vtracer", colors=4, detail="low", meta=True),
    )
    assert output.is_file()
    assert result.path_count > 0
    assert result.bytes == output.stat().st_size
    meta = meta_path_for(output)
    assert meta.is_file()
    assert "vtracer" in meta.read_text(encoding="utf-8")
