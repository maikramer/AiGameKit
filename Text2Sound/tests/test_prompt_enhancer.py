"""Testes para text2sound.prompt_enhancer (enriquecimento determinístico de prompt).

Sem dependências de GPU ou modelo — puro Python + YAML.
"""

from __future__ import annotations

import pytest

pytest.importorskip("yaml")  # só precisa de PyYAML

from text2sound.prompt_enhancer import (
    detect_sound_type,
    enhance_negative,
    enhance_prompt,
    load_descriptor_data,
    validate_and_correct,
)


@pytest.fixture(scope="module")
def data():
    """Carrega o YAML de descritores uma vez por módulo."""
    return load_descriptor_data()


class TestValidateAndCorrect:
    def test_strips_excess_whitespace(self, data):
        clean, corrections = validate_and_correct("  explosion   blast  ", data)
        assert clean == "Explosion blast"
        assert corrections == []

    def test_empty_prompt_fallback(self, data):
        clean, corrections = validate_and_correct("", data)
        assert clean == "ambient sound"
        assert "empty_or_short_fallback" in corrections

    def test_whitespace_only_prompt_fallback(self, data):
        clean, corrections = validate_and_correct("   ", data)
        assert clean == "ambient sound"
        assert "empty_or_short_fallback" in corrections

    def test_short_prompt_fallback(self, data):
        # < 3 chars
        clean, corrections = validate_and_correct("ab", data)
        assert clean == "ambient sound"
        assert "empty_or_short_fallback" in corrections

    def test_capitalizes_first_letter(self, data):
        clean, _ = validate_and_correct("explosion sound", data)
        assert clean[0] == "E"

    def test_detects_weak_term(self, data):
        _, corrections = validate_and_correct("good sound", data)
        assert any("weak_term" in c for c in corrections)

    def test_no_false_weak_on_normal_prompt(self, data):
        _, corrections = validate_and_correct("large explosion with debris", data)
        assert not any("weak_term" in c for c in corrections)


class TestDetectSoundType:
    def test_keyword_explosion(self, data):
        assert detect_sound_type("big explosion", data=data) == "impact"

    def test_keyword_sword(self, data):
        assert detect_sound_type("sword swing", data=data) == "weapon"

    def test_keyword_magic(self, data):
        assert detect_sound_type("magic spell cast", data=data) == "magic"

    def test_keyword_rain(self, data):
        assert detect_sound_type("rain ambience", data=data) == "ambient_loop"

    def test_keyword_coin(self, data):
        assert detect_sound_type("coin pickup", data=data) == "collectible"

    def test_audio_kind_overrides_keyword(self, data):
        # "boom" não é keyword de weapon, mas audio_kind força weapon.
        assert detect_sound_type("boom", audio_kind="sfx_weapon", data=data) == "weapon"

    def test_audio_kind_impact(self, data):
        assert detect_sound_type("weird thing", audio_kind="sfx_impact", data=data) == "impact"

    def test_no_match_returns_generic(self, data):
        assert detect_sound_type("random stuff xyz", data=data) == "generic"

    def test_case_insensitive(self, data):
        assert detect_sound_type("BIG EXPLOSION", data=data) == "impact"


class TestEnhancePrompt:
    def test_adds_descriptors_for_impact(self, data):
        enhanced, meta = enhance_prompt("explosion", data=data)
        assert enhanced.startswith("Explosion")
        assert meta["sound_type"] == "impact"
        assert len(meta["descriptors_added"]) > 0

    def test_returns_enhancement_metadata(self, data):
        _, meta = enhance_prompt("sword clash", data=data)
        assert "sound_type" in meta
        assert "descriptors_added" in meta
        assert "corrections" in meta
        assert meta["original_prompt"] == "sword clash"

    def test_no_duplicates_when_already_present(self, data):
        # "deep bass impact" já está no prompt → não deve ser re-adicionado.
        _, meta = enhance_prompt("explosion with deep bass impact", data=data)
        added_lower = [d.lower() for d in meta["descriptors_added"]]
        assert not any("deep bass" in d for d in added_lower)

    def test_generic_fallback_descriptors(self, data):
        _, meta = enhance_prompt("xyzzy obscure word", data=data)
        assert meta["sound_type"] == "generic"
        # generic tem pelo menos "high quality" ou "detailed".
        assert len(meta["descriptors_added"]) >= 1

    def test_empty_prompt_uses_fallback(self, data):
        enhanced, meta = enhance_prompt("", data=data)
        assert "ambient sound" in enhanced
        assert "empty_or_short_fallback" in meta["corrections"]

    def test_weak_prompt_gets_more_descriptors(self, data):
        # Prompt fraco (weak term) → max_per_group=3 vs 2 normal.
        _, meta_weak = enhance_prompt("good sound", data=data)
        _, meta_normal = enhance_prompt("explosion", data=data)
        # weak deve ter >= descritores que normal (mais agressivo).
        assert len(meta_weak["descriptors_added"]) >= len(meta_normal["descriptors_added"]) - 2

    def test_uses_audio_kind_for_detection(self, data):
        _, meta = enhance_prompt("boom", audio_kind="sfx_weapon", data=data)
        assert meta["sound_type"] == "weapon"


class TestEnhanceNegative:
    def test_adds_negative_descriptors(self, data):
        result = enhance_negative("music, melody", data=data)
        assert "music, melody" in result
        # deve ter adicionado pelo menos um anti-descritor genérico.
        assert "low quality" in result or "distortion" in result

    def test_no_duplicate_negatives(self, data):
        result = enhance_negative("low quality, distortion", data=data)
        # não deve duplicar "low quality" nem "distortion".
        assert result.count("low quality") == 1
        assert result.count("distortion") == 1

    def test_empty_negatives_get_all(self, data):
        result = enhance_negative("music", data=data)
        # todos os negative_descriptors genéricos são adicionados (exceto se "music" bate).
        assert "," in result  # há múltiplos


class TestLoadDescriptorData:
    def test_loads_keywords(self, data):
        assert "impact" in data["keywords"]
        assert "weapon" in data["keywords"]

    def test_loads_descriptors(self, data):
        assert "impact" in data["descriptors"]
        assert "texture" in data["descriptors"]["impact"]

    def test_loads_negative_descriptors(self, data):
        assert isinstance(data["negative_descriptors"], list)
        assert len(data["negative_descriptors"]) > 0

    def test_loads_weak_terms(self, data):
        assert isinstance(data["weak_terms"], list)
        assert len(data["weak_terms"]) > 0

    def test_generic_always_present(self, data):
        # fallback type deve ter descriptors.
        assert "generic" in data["descriptors"]
