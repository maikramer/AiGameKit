"""Testes para o prompt enhancer de chão top-down (SD1.5)."""

from __future__ import annotations

from texture2d.prompt_enhancer import (
    GROUND_DEFAULT_NEGATIVE,
    GROUND_SUFFIX,
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
    def test_appends_suffix(self) -> None:
        out = enhance_ground_prompt("grass meadow")
        assert "grass meadow" in out
        assert GROUND_SUFFIX in out

    def test_subject_comes_before_suffix(self) -> None:
        """O sujeito deve vir antes do sufixo (janela CLIP de 77 tokens)."""
        out = enhance_ground_prompt("lush grass")
        subject_pos = out.find("lush grass")
        suffix_pos = out.find("top-down close-up")
        assert 0 <= subject_pos < suffix_pos

    def test_positive_language_only(self) -> None:
        # Negação no prompt positivo injeta o conceito indesejado no embedding.
        out = enhance_ground_prompt("grass meadow")
        assert "no " not in out.lower()
        assert "not " not in out.lower()

    def test_skips_if_already_covered(self) -> None:
        """Se o utilizador já incluiu viewpoint+iluminação+escala, não duplica."""
        p = "top-down close-up grass, flat diffuse lighting, seamless tileable"
        out = enhance_ground_prompt(p)
        # O sufixo não é adicionado — o prompt fica inalterado (após truncate).
        assert GROUND_SUFFIX not in out
        assert "top-down" in out

    def test_empty_prompt(self) -> None:
        assert enhance_ground_prompt("") == ""
        assert enhance_ground_prompt("   ") == ""

    def test_respects_word_budget(self) -> None:
        long_subject = " ".join(["detail"] * 100)
        out = enhance_ground_prompt(long_subject)
        assert len(out.split()) <= 60

    def test_preserves_subject(self) -> None:
        out = enhance_ground_prompt("desert sand")
        assert "desert sand" in out


class TestEnhanceGroundNegative:
    def test_empty_returns_default(self) -> None:
        out = enhance_ground_negative("")
        assert out == GROUND_DEFAULT_NEGATIVE

    def test_merges_with_user_negative(self) -> None:
        out = enhance_ground_negative("blurry, low quality")
        assert "blurry" in out
        assert "low quality" in out
        assert "macro" in out  # do default

    def test_dedup_macros(self) -> None:
        out = enhance_ground_negative("macro photography, extreme close-up")
        # Não duplica termos já presentes no negative do utilizador
        assert out.count("macro photography") == 1
        assert out.count("extreme close-up") == 1

    def test_default_content_present(self) -> None:
        out = enhance_ground_negative("")
        for term in ["isometric", "perspective", "oblique angle", "deep relief", "ambient occlusion"]:
            assert term in out


def test_ground_constants_nonempty() -> None:
    assert len(GROUND_SUFFIX) > 20
    assert "top-down" in GROUND_SUFFIX
    assert "macro" in GROUND_DEFAULT_NEGATIVE
    assert "aerial" in GROUND_DEFAULT_NEGATIVE
