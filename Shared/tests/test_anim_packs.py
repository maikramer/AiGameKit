"""Gramática de combinação de packs de animação (expand_anim_packs) — CPU-first."""

from __future__ import annotations

import pytest

from aigamekit_shared.anim_packs import ANIM_PACK_TOKENS, AnimPackError, expand_anim_packs


class TestExpandAnimPacks:
    def test_single_packs_pass_through(self) -> None:
        assert expand_anim_packs("quaternius") == ["quaternius"]
        assert expand_anim_packs("quaternius2") == ["quaternius2"]
        assert expand_anim_packs("villager") == ["villager"]

    def test_both_is_ual1_then_ual2(self) -> None:
        assert expand_anim_packs("both") == ["quaternius", "quaternius2"]

    def test_all_runs_villager_first(self) -> None:
        """Ordem canónica do all: villager primeiro; a UAL substitui colisões."""
        assert expand_anim_packs("all") == ["villager", "quaternius", "quaternius2"]

    def test_comma_list_preserves_order(self) -> None:
        assert expand_anim_packs("quaternius2,villager") == ["quaternius2", "villager"]
        assert expand_anim_packs("both,villager") == ["quaternius", "quaternius2", "villager"]

    def test_case_insensitive_and_spaces(self) -> None:
        assert expand_anim_packs("  BOTH , Villager ") == ["quaternius", "quaternius2", "villager"]

    def test_dedup_keeps_first_position(self) -> None:
        assert expand_anim_packs("villager,all") == ["villager", "quaternius", "quaternius2"]
        assert expand_anim_packs("quaternius2,quaternius2") == ["quaternius2"]

    def test_unknown_token_raises(self) -> None:
        with pytest.raises(AnimPackError, match="mixamo"):
            expand_anim_packs("mixamo")

    def test_unknown_token_in_list_raises(self) -> None:
        with pytest.raises(AnimPackError, match="mixamo"):
            expand_anim_packs("both,mixamo")

    def test_empty_raises(self) -> None:
        with pytest.raises(AnimPackError, match="vazio"):
            expand_anim_packs("   ")

    def test_tokens_documented(self) -> None:
        for token in ("quaternius", "quaternius2", "villager", "both", "all"):
            assert token in ANIM_PACK_TOKENS
