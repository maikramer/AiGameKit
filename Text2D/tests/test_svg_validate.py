"""Extracção, reparação e sanitização de SVG."""

from __future__ import annotations

import pytest

from text2d.svg.validate import (
    SvgValidationError,
    check_size,
    count_svg_shapes,
    normalize_svg,
)


def test_extract_fenced_svg_with_prose():
    raw = 'Here is the icon:\n```svg\n<svg viewBox="0 0 10 10"><path d="M0 0 L10 10"/></svg>\n```\nDone.'
    svg, paths = normalize_svg(raw, width=10, height=10)
    assert svg.startswith("<svg")
    assert paths == 1


def test_repair_truncated_svg():
    raw = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20"><g><path d="M0 0 L20 20"/>'
    svg, paths = normalize_svg(raw, width=20, height=20)
    assert "</g></svg>" in svg
    assert paths == 1


def test_strip_script_foreignobject_and_external_href():
    raw = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
        "<script>alert(1)</script>"
        '<foreignObject><div xmlns="http://www.w3.org/1999/xhtml">x</div></foreignObject>'
        '<use href="https://evil.example/x.svg"/>'
        '<path d="M0 0 L10 10" onclick="alert(1)"/>'
        "</svg>"
    )
    svg, _ = normalize_svg(raw, width=10, height=10)
    assert "script" not in svg
    assert "foreignObject" not in svg
    assert "evil.example" not in svg
    assert "onclick" not in svg
    assert "path" in svg


def test_dimensions_are_normalized_to_source_size():
    raw = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256"><path d="M0 0 L10 10"/></svg>'
    svg, _ = normalize_svg(raw, width=512, height=512)
    assert 'width="512"' in svg
    assert 'height="512"' in svg
    assert 'viewBox="0 0 256 256"' in svg


def test_missing_viewbox_is_derived_from_width_height():
    raw = '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="50"><rect width="100" height="50"/></svg>'
    svg, _ = normalize_svg(raw)
    assert 'viewBox="0 0 100 50"' in svg


def test_invalid_text_raises():
    with pytest.raises(SvgValidationError):
        normalize_svg("not an svg at all")


def test_count_svg_shapes():
    svg = '<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0"/><rect width="1" height="1"/><defs/></svg>'
    assert count_svg_shapes(svg) == 2


def test_check_size_strict_and_warning():
    svg = "<svg>" + ("x" * 4096) + "</svg>"
    warning = check_size(svg, max_kb=2, strict=False)
    assert warning and "excede" in warning
    with pytest.raises(SvgValidationError):
        check_size(svg, max_kb=2, strict=True)
