"""Testes para ``text3d.utils.prompt_enhance`` — pipeline de regex e modificadores."""

from __future__ import annotations

import random

from text3d.utils import prompt_enhance as pe


class TestSanitizePrompt:
    def test_empty_string(self) -> None:
        assert pe.sanitize_prompt("") == ""

    def test_no_toxic_terms_unchanged(self) -> None:
        assert pe.sanitize_prompt("a red car") == "a red car"

    def test_removes_single_toxic_term(self) -> None:
        assert pe.sanitize_prompt("a spotlight") == "a"

    def test_case_insensitive(self) -> None:
        assert pe.sanitize_prompt("A CHAIR ON THE FLOOR") == "A CHAIR"

    def test_mixed_case_term_is_case_insensitive(self) -> None:
        # O padrao do termo toxico usa re.IGNORECASE: "Dramatic Lighting" e' removido.
        assert pe.sanitize_prompt("a Lamp with dramatic lighting") == "a Lamp"

    def test_capital_with_survives_cleanup(self) -> None:
        # O cleanup `\bwith\s*$` NAO usa IGNORECASE: "With" maiusculo permanece.
        assert pe.sanitize_prompt("a Lamp With Dramatic Lighting") == "a Lamp With"

    def test_multiple_toxic_terms(self) -> None:
        assert pe.sanitize_prompt("a box on the floor with dramatic lighting") == "a box"

    def test_term_as_substring_of_word(self) -> None:
        # Sem word boundary no regex: "spotlight" dentro de "spotlights" e' removido.
        assert pe.sanitize_prompt("spotlights everywhere") == "s everywhere"

    def test_removes_standing_on_phrase(self) -> None:
        assert pe.sanitize_prompt("a goblin standing on a pedestal") == "a goblin"

    def test_trailing_dangling_standing(self) -> None:
        # O regex de dangling precisa de `\s+` apos "standing" (aqui " and").
        assert pe.sanitize_prompt("a warrior standing and") == "a warrior"

    def test_trailing_dangling_sitting(self) -> None:
        assert pe.sanitize_prompt("a statue sitting and") == "a statue"

    def test_bare_trailing_standing_not_removed(self) -> None:
        # Sem whitespace apos "standing", o regex de dangling nao dispara.
        assert pe.sanitize_prompt("a warrior standing") == "a warrior standing"

    def test_trailing_and_removed(self) -> None:
        assert pe.sanitize_prompt("a sword and") == "a sword"

    def test_collapses_whitespace(self) -> None:
        assert pe.sanitize_prompt("a    car") == "a car"

    def test_collapses_multiple_commas(self) -> None:
        assert pe.sanitize_prompt("a sword,,, a shield") == "a sword, a shield"

    def test_strips_leading_trailing_punctuation(self) -> None:
        assert pe.sanitize_prompt(", a car, ") == "a car"

    def test_longest_term_first(self) -> None:
        # "ambient occlusion on ground" deve ser removido antes de "ground shadow".
        result = pe.sanitize_prompt("a mesh with ambient occlusion on ground")
        assert "ambient occlusion on ground" not in result.lower()

    def test_all_toxic_terms_removable(self) -> None:
        for term in pe.TOXIC_TERMS:
            assert pe.sanitize_prompt(term) == ""


class TestEnhancePromptForCleanBase:
    def test_skip_when_clean_marker_present(self) -> None:
        prompt = "a dragon, albedo render"
        assert pe.enhance_prompt_for_clean_base(prompt) == prompt

    def test_skip_when_shadowless_marker(self) -> None:
        prompt = "a dragon, shadowless"
        assert pe.enhance_prompt_for_clean_base(prompt) == prompt

    def test_aggressive_wraps_with_full_prefix_suffix(self) -> None:
        result = pe.enhance_prompt_for_clean_base("a dragon")
        assert result == f"{pe._RENDER_PREFIX}, a dragon, {pe._RENDER_SUFFIX}"

    def test_light_wraps_with_short_prefix_suffix(self) -> None:
        result = pe.enhance_prompt_for_clean_base("a dragon", aggressive=False)
        assert result == f"{pe._RENDER_PREFIX_LIGHT}, a dragon, {pe._RENDER_SUFFIX_LIGHT}"

    def test_strips_prompt_whitespace(self) -> None:
        result = pe.enhance_prompt_for_clean_base("  a dragon  ")
        assert result == f"{pe._RENDER_PREFIX}, a dragon, {pe._RENDER_SUFFIX}"


class TestCreateOptimizedPrompt:
    def test_pipeline_sanitize_then_enhance(self) -> None:
        result = pe.create_optimized_prompt("a chair on the floor")
        clean = pe.sanitize_prompt("a chair on the floor")
        expected = pe.enhance_prompt_for_clean_base(clean)
        assert result == expected

    def test_toxic_term_removed_before_wrap(self) -> None:
        result = pe.create_optimized_prompt("a chair on the floor")
        assert "on the floor" not in result.lower()
        assert pe._RENDER_PREFIX in result

    def test_keeps_marker_prompt_through_pipeline(self) -> None:
        prompt = "a dragon, albedo render"
        assert pe.create_optimized_prompt(prompt) == prompt


class TestModifyPromptForRetry:
    def _shuffled_mods(self) -> list[str]:
        rng = random.Random(42)
        mods = list(pe.RETRY_PROMPT_MODIFIERS)
        rng.shuffle(mods)
        return mods

    def test_deterministic_same_attempt(self) -> None:
        assert pe.modify_prompt_for_retry("dragon", 1) == pe.modify_prompt_for_retry("dragon", 1)

    def test_attempt_1_prepends_first_shuffled_mod(self) -> None:
        first = self._shuffled_mods()[0]
        assert pe.modify_prompt_for_retry("dragon", 1) == f"{first} dragon"

    def test_different_attempts_different_modifier(self) -> None:
        mods = self._shuffled_mods()
        a1 = pe.modify_prompt_for_retry("dragon", 1)
        a2 = pe.modify_prompt_for_retry("dragon", 2)
        assert a1 != a2
        assert a1 == f"{mods[0]} dragon"
        assert a2 == f"{mods[1]} dragon"

    def test_wraps_around_modulo_length(self) -> None:
        n = len(pe.RETRY_PROMPT_MODIFIERS)
        assert pe.modify_prompt_for_retry("dragon", 1) == pe.modify_prompt_for_retry("dragon", 1 + n)

    def test_returns_modifier_from_known_set(self) -> None:
        result = pe.modify_prompt_for_retry("dragon", 3)
        assert result.split(" ", 1)[0] in pe.RETRY_PROMPT_MODIFIERS

    def test_skips_modifier_already_in_prompt(self) -> None:
        mods = self._shuffled_mods()
        first, second = mods[0], mods[1]
        prompt_with_first = f"{first} a dragon"
        result = pe.modify_prompt_for_retry(prompt_with_first, attempt=1)
        assert result == f"{second} {prompt_with_first}"

    def test_explicit_rng_respected(self) -> None:
        ref = random.Random(123)
        mods = list(pe.RETRY_PROMPT_MODIFIERS)
        ref.shuffle(mods)
        expected = f"{mods[0]} dragon"
        assert pe.modify_prompt_for_retry("dragon", 1, rng=random.Random(123)) == expected
