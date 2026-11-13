"""Prompt enhancement determinístico para SVG."""

from __future__ import annotations

from text2d.icons import augment_prompt_for_icon
from text2d.svg.prompt import enhance_prompt_for_svg


def test_replaces_raster_terms_and_preserves_subject_and_avoid():
    prompt = (
        "hand-painted game art, visible brush strokes, a blue dash swirl, "
        "Avoid: realistic, gore"
    )
    out = enhance_prompt_for_svg(prompt, style="flat", colors=8)

    assert "brush strokes" not in out.lower()
    assert "a blue dash swirl" in out
    assert "flat vector illustration" in out
    assert "8 colors" in out
    # O bloco negativo é preservado literalmente (o enhancer não o reescreve).
    assert "Avoid: realistic, gore" in out


def test_idempotent():
    once = enhance_prompt_for_svg("a red apple", style="flat", colors=8)
    twice = enhance_prompt_for_svg(once, style="flat", colors=8)
    assert once == twice


def test_negations_are_not_flipped():
    out = enhance_prompt_for_svg("a gem, no gradients, no shadows", style="flat", colors=4)
    assert "no solid flat colors" not in out
    assert "no gradients" in out
    assert out.count("no shadows") == 1


def test_line_and_detailed_styles_have_distinct_markers():
    line = enhance_prompt_for_svg("a sword", style="line")
    detailed = enhance_prompt_for_svg("a forest spirit", style="detailed", colors=16)
    assert "monochrome line art" in line
    assert "stylized flat illustration" in detailed
    assert "16 colors" in detailed


def test_icon_augmentation_then_svg():
    prompt = augment_prompt_for_icon("a dash swirl")
    out = enhance_prompt_for_svg(prompt, style="flat", colors=8)
    assert "app icon" in out
    assert "flat vector illustration" in out


def test_empty_prompt_is_returned_unchanged():
    assert enhance_prompt_for_svg("") == ""
