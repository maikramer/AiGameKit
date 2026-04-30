"""Testes para text2sound.presets."""

import pytest

from text2sound.presets import AUDIO_PRESETS, get_preset, list_presets


class TestListPresets:
    def test_returns_sorted(self):
        names = list_presets()
        assert names == sorted(names)

    def test_not_empty(self):
        assert len(list_presets()) > 0

    def test_count(self):
        assert len(list_presets()) == 60

    def test_known_presets(self):
        names = list_presets()
        assert "ambient" in names
        assert "battle" in names
        assert "menu" in names
        assert "rain" in names
        assert "explosion" in names

    def test_new_presets(self):
        names = list_presets()
        # New categories
        assert "cave" in names
        assert "city" in names
        assert "desert" in names
        assert "space" in names
        assert "underwater" in names
        assert "victory" in names
        assert "defeat" in names
        assert "exploration" in names
        assert "boss" in names
        assert "punch" in names
        assert "gunshot" in names
        assert "arrow" in names
        assert "heal" in names
        assert "teleport" in names
        assert "shield" in names
        assert "footsteps-wood" in names
        assert "footsteps-water" in names
        assert "ui-cancel" in names
        assert "ui-hover" in names
        assert "creature-growl" in names
        assert "creature-roar" in names
        assert "creature-death" in names
        # SFX Destruction
        assert "glass-break" in names
        assert "wood-break" in names
        assert "stone-crumble" in names
        # SFX Weapon
        assert "sword-draw" in names
        assert "bow-draw" in names
        assert "weapon-reload" in names
        # SFX Mechanical
        assert "door-open" in names
        assert "door-close" in names
        assert "lever" in names
        assert "clockwork" in names
        # SFX Elemental
        assert "fire-crackle" in names
        assert "water-splash" in names
        assert "electricity-zap" in names
        # SFX Vocal
        assert "grunt-effort" in names
        assert "battle-cry" in names
        assert "death-scream" in names
        # SFX Collectible
        assert "coin-pickup" in names
        assert "gem-collect" in names
        assert "item-drop" in names
        # SFX Alarm
        assert "alarm-klaxon" in names
        assert "bell-toll" in names
        # SFX Ambient Spot
        assert "thunder-clap" in names


class TestGetPreset:
    def test_exact_match(self):
        p = get_preset("ambient")
        assert "prompt" in p
        assert "duration" in p
        assert "steps" in p
        assert "cfg_scale" in p
        assert "kind" in p

    def test_case_insensitive(self):
        p = get_preset("BATTLE")
        assert "prompt" in p

    def test_underscore_to_hyphen(self):
        p = get_preset("footsteps_stone")
        assert "prompt" in p

    def test_unknown_raises(self):
        with pytest.raises(KeyError, match="Preset desconhecido"):
            get_preset("nao_existe_este_preset")

    def test_kind_field(self):
        p = get_preset("ambient")
        assert p["kind"] == "ambient_loop"
        p = get_preset("battle")
        assert p["kind"] == "music_loop"
        p = get_preset("explosion")
        assert p["kind"] == "sfx_impact"


class TestPresetStructure:
    @pytest.mark.parametrize("name", list_presets())
    def test_required_fields(self, name):
        p = AUDIO_PRESETS[name]
        assert isinstance(p["prompt"], str) and len(p["prompt"]) > 0
        assert isinstance(p["duration"], (int, float)) and p["duration"] > 0
        assert isinstance(p["steps"], int) and p["steps"] > 0
        assert isinstance(p["cfg_scale"], (int, float)) and p["cfg_scale"] > 0
        assert "kind" in p
        assert isinstance(p["kind"], str) and len(p["kind"]) > 0

    @pytest.mark.parametrize("name", list_presets())
    def test_valid_audio_kind(self, name):
        """Every preset kind must be a known audio_kind from asset-categories.yaml."""
        p = AUDIO_PRESETS[name]
        valid_kinds = {
            "ambient_loop",
            "music_loop",
            "sfx_impact",
            "sfx_magic",
            "sfx_movement",
            "sfx_ui",
            "sfx_creature",
            "ambient_one_shot",
            "sfx_short",
            "sfx_vehicle",
            "sfx_interact",
            "sfx_destruction",
            "sfx_weapon",
            "sfx_mechanical",
            "sfx_elemental",
            "sfx_vocal",
            "sfx_collectible",
            "sfx_alarm",
            "sfx_ambient_sfx",
        }
        assert p["kind"] in valid_kinds, f"{name}: kind={p['kind']!r} not in {valid_kinds}"

    @pytest.mark.parametrize("name", list_presets())
    def test_duration_within_model_limits(self, name):
        p = AUDIO_PRESETS[name]
        assert 0 < p["duration"] <= 47

    @pytest.mark.parametrize("name", list_presets())
    def test_steps_reasonable(self, name):
        p = AUDIO_PRESETS[name]
        assert 10 <= p["steps"] <= 200

    @pytest.mark.parametrize("name", list_presets())
    def test_cfg_scale_reasonable(self, name):
        p = AUDIO_PRESETS[name]
        assert 1.0 <= p["cfg_scale"] <= 15.0

    def test_ambience_durations(self):
        for name in ("ambient", "forest", "ocean", "rain"):
            p = AUDIO_PRESETS[name]
            assert p["duration"] == 45
        for name in ("wind", "dungeon", "tavern", "cave", "city", "desert", "space", "underwater"):
            p = AUDIO_PRESETS[name]
            assert p["duration"] == 30

    def test_sfx_ui_durations(self):
        p = AUDIO_PRESETS["ui-click"]
        assert p["duration"] == 1
        p = AUDIO_PRESETS["ui-confirm"]
        assert p["duration"] == 1.5
        p = AUDIO_PRESETS["ui-cancel"]
        assert p["duration"] == 1
        p = AUDIO_PRESETS["ui-hover"]
        assert p["duration"] == 0.5
