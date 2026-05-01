"""Testes para text2sound.models."""

import pytest

from text2sound.models import (
    MODEL_EFFECTS_ID,
    MODEL_MUSIC_ID,
    get_spec,
    resolve_model_from_profile,
    resolve_model_id,
)


class TestResolveModelId:
    def test_none_is_music(self):
        assert resolve_model_id(None) == MODEL_MUSIC_ID

    def test_empty_string_is_music(self):
        assert resolve_model_id("") == MODEL_MUSIC_ID
        assert resolve_model_id("   ") == MODEL_MUSIC_ID

    def test_aliases_music(self):
        assert resolve_model_id("music") == MODEL_MUSIC_ID
        assert resolve_model_id("MUSIC") == MODEL_MUSIC_ID
        assert resolve_model_id("full") == MODEL_MUSIC_ID
        assert resolve_model_id("1.0") == MODEL_MUSIC_ID

    def test_aliases_effects(self):
        assert resolve_model_id("effects") == MODEL_EFFECTS_ID
        assert resolve_model_id("small") == MODEL_EFFECTS_ID
        assert resolve_model_id("sfx") == MODEL_EFFECTS_ID

    def test_hf_id_passthrough(self):
        assert resolve_model_id("stabilityai/stable-audio-open-1.0") == MODEL_MUSIC_ID
        assert resolve_model_id("stabilityai/stable-audio-open-small") == MODEL_EFFECTS_ID

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Modelo desconhecido"):
            resolve_model_id("not_an_alias")


class TestResolveModelFromProfile:
    def test_profile_effects_no_override(self):
        assert resolve_model_from_profile("effects", None) == MODEL_EFFECTS_ID

    def test_profile_music_no_override(self):
        assert resolve_model_from_profile("music", None) == MODEL_MUSIC_ID

    def test_model_override_wins(self):
        assert resolve_model_from_profile("music", "effects") == MODEL_EFFECTS_ID
        assert resolve_model_from_profile("effects", "music") == MODEL_MUSIC_ID


class TestGetSpec:
    def test_known_ids(self):
        m = get_spec(MODEL_MUSIC_ID)
        assert m.max_seconds == 47.0
        assert m.default_steps == 100
        e = get_spec(MODEL_EFFECTS_ID)
        assert e.max_seconds == 11.0
        assert e.default_steps == 8
        assert e.default_sampler == "euler"

    def test_custom_id_fallback(self):
        s = get_spec("user/custom-model")
        assert s.max_seconds == 47.0
