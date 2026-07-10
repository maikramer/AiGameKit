"""Testes para o prompt enhancer de chão top-down."""

from __future__ import annotations

from texture2d.prompt_enhancer import (
    GROUND_DEFAULT_NEGATIVE,
    GROUND_LIGHTING,
    GROUND_SCALE,
    GROUND_VIEWPOINT,
    enhance_ground_negative,
    enhance_ground_prompt,
    looks_like_ground,
)


class TestLooksLikeGround:
    def test_grass_is_ground(self) -> None:
        assert looks_like_ground("green grass meadow") is True

    def test_dirt_is_ground(self) -> None:
        assert looks_like_ground("brown dirt soil earth") is True

    def test_desert_sand_is_ground(self) -> None:
        assert looks_like_ground("desert sand with rocks") is True

    def test_stone_is_ground(self) -> None:
        assert looks_like_ground("grey stone rock surface") is True

    def test_metal_is_not_ground(self) -> None:
        assert looks_like_ground("brushed steel metal panel") is False

    def test_empty_is_not_ground(self) -> None:
        assert looks_like_ground("") is False
        assert looks_like_ground("   ") is False


class TestEnhanceGroundPrompt:
    def test_adds_all_modifiers(self) -> None:
        out = enhance_ground_prompt("grass meadow")
        assert "top-down orthographic" in out
        assert "flat diffuse lighting" in out
        assert "medium-scale" in out
        assert "grass meadow" in out

    def test_viewpoint_prefix_comes_first(self) -> None:
        # Os modificadores de composição devem vir antes do sujeito para entrar
        # na janela de 77 tokens do CLIP.
        out = enhance_ground_prompt("lush grass")
        vp_pos = out.find("flat top-down")
        subject_pos = out.find("lush grass")
        assert vp_pos < subject_pos

    def test_skips_viewpoint_if_present(self) -> None:
        out = enhance_ground_prompt("top-down view of grass", viewpoint=True, lighting=False, scale=False)
        # Já tem "top-down" — não duplica o viewpoint
        assert out.count("top-down") == 1

    def test_skips_lighting_if_present(self) -> None:
        out = enhance_ground_prompt("grass, flat lighting", viewpoint=False, lighting=True, scale=False)
        assert out.count("flat diffuse lighting") == 0  # já tem flat lighting
        assert "grass" in out

    def test_selective_flags(self) -> None:
        out = enhance_ground_prompt("grass", viewpoint=True, lighting=False, scale=False)
        assert "top-down" in out
        assert "diffuse lighting" not in out
        assert "medium-scale" not in out

    def test_empty_prompt(self) -> None:
        assert enhance_ground_prompt("") == ""
        assert enhance_ground_prompt("   ") == ""

    def test_respects_word_budget(self) -> None:
        long_subject = " ".join(["detail"] * 100)
        out = enhance_ground_prompt(long_subject)
        # Não deve exceder o orçamento brando (55 palavras) + uma margem pequena.
        assert len(out.split()) <= 60

    def test_no_modifiers_needed_returns_truncated(self) -> None:
        # Se todos os modificadores já estão presentes, só trunca.
        p = f"top-down overhead view, flat lighting, low relief, medium-scale, not macro. {'grass ' * 30}"
        out = enhance_ground_prompt(p)
        assert len(out.split()) <= 60

    def test_preserves_subject_at_end(self) -> None:
        out = enhance_ground_prompt("desert sand")
        assert out.endswith("desert sand")


class TestEnhanceGroundNegative:
    def test_empty_returns_default(self) -> None:
        out = enhance_ground_negative("")
        assert out == GROUND_DEFAULT_NEGATIVE

    def test_merges_with_user_negative(self) -> None:
        out = enhance_ground_negative("blurry, low quality")
        assert "blurry" in out
        assert "low quality" in out
        assert "macro" in out  # do default

    def test_dedup_macro(self) -> None:
        out = enhance_ground_negative("macro, close-up")
        # Não duplica "macro"
        assert out.count("macro") == 1
        assert out.count("close-up") == 1

    def test_default_content_present(self) -> None:
        out = enhance_ground_negative("")
        for term in ["isometric", "perspective", "angled", "3d relief", "ambient occlusion"]:
            assert term in out


def test_ground_constants_nonempty() -> None:
    assert len(GROUND_VIEWPOINT) > 20
    assert len(GROUND_LIGHTING) > 20
    assert len(GROUND_SCALE) > 20
    assert "macro" in GROUND_DEFAULT_NEGATIVE
