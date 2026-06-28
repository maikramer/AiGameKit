"""Testes unitários para gameassets.dream.llm_context (system prompt + schema)."""

from __future__ import annotations

import pytest

from gameassets.dream.llm_context import (
    DREAM_PLAN_SCHEMA,
    SCENE_RULES,
    VIBEGAME_RECIPES,
    build_system_prompt,
)


def _default_prompt(**overrides: object) -> str:
    base: dict[str, object] = {
        "preset_names": ["lowpoly", "painterly"],
        "max_assets": 8,
        "with_audio": True,
        "with_sky": True,
    }
    base.update(overrides)
    return build_system_prompt(**base)  # type: ignore[arg-type]


class TestSystemPromptAnchors:
    def test_contains_schema_block(self) -> None:
        prompt = _default_prompt()
        assert "JSON Schema" in prompt
        for field in ("title", "genre", "tone", "style_preset", "assets", "scene"):
            assert f'"{field}"' in prompt

    def test_contains_scene_rules(self) -> None:
        prompt = _default_prompt()
        assert "Scene layout rules" in prompt
        assert SCENE_RULES in prompt

    def test_contains_preset_names(self) -> None:
        prompt = _default_prompt(preset_names=["lowpoly", "painterly"])
        assert "lowpoly" in prompt
        assert "painterly" in prompt

    def test_contains_max_assets_limit(self) -> None:
        prompt = _default_prompt(max_assets=12)
        assert "Maximum total assets: 12" in prompt

    def test_falls_back_to_lowpoly_when_no_presets(self) -> None:
        prompt = _default_prompt(preset_names=[])
        assert "lowpoly" in prompt

    def test_includes_vibegame_recipes(self) -> None:
        prompt = _default_prompt()
        for recipe in ("PlayerGLTF", "GLTFLoader", "Terrain", "OrbitCamera"):
            assert recipe in prompt
        assert "PlayerGLTF" in VIBEGAME_RECIPES


class TestAudioSkyToggles:
    def test_audio_enabled_note(self) -> None:
        prompt = _default_prompt(with_audio=True)
        assert "You may include audio assets" in prompt
        assert "Do NOT include audio assets" not in prompt

    def test_audio_disabled_note(self) -> None:
        prompt = _default_prompt(with_audio=False)
        assert "Do NOT include audio assets" in prompt
        assert "You may include audio assets" not in prompt

    def test_sky_enabled_note(self) -> None:
        prompt = _default_prompt(with_sky=True)
        assert "Include a sky_prompt field" in prompt

    def test_sky_disabled_note(self) -> None:
        prompt = _default_prompt(with_sky=False)
        assert "Do NOT include sky_prompt" in prompt

    def test_audio_toggle_changes_prompt(self) -> None:
        on = _default_prompt(with_audio=True)
        off = _default_prompt(with_audio=False)
        assert on != off

    def test_sky_toggle_changes_prompt(self) -> None:
        on = _default_prompt(with_sky=True)
        off = _default_prompt(with_sky=False)
        assert on != off

    def test_max_assets_changes_prompt(self) -> None:
        low = _default_prompt(max_assets=3)
        high = _default_prompt(max_assets=20)
        assert low != high


class TestSchemaShape:
    def test_required_top_level_keys(self) -> None:
        required = DREAM_PLAN_SCHEMA["required"]
        for key in ("title", "genre", "tone", "style_preset", "assets", "scene"):
            assert key in required

    def test_asset_kinds_are_enum(self) -> None:
        asset_item = DREAM_PLAN_SCHEMA["properties"]["assets"]["items"]
        assert asset_item["properties"]["kind"]["enum"] == ["prop", "character", "environment"]

    def test_terrain_is_optional(self) -> None:
        assert "terrain" not in DREAM_PLAN_SCHEMA["required"]
        assert "terrain" in DREAM_PLAN_SCHEMA["properties"]
        assert "enabled" in DREAM_PLAN_SCHEMA["properties"]["terrain"]["properties"]


@pytest.mark.parametrize("tier", ["fast", "low", "medium", "high", "highest"])
def test_prompt_stable_across_tier_names(tier: str) -> None:
    # O system prompt não depende do tier de qualidade (só dos preset names);
    # garantir que qualquer lista de presets produz um prompt não vazio.
    prompt = build_system_prompt(preset_names=[tier], max_assets=8)
    assert isinstance(prompt, str)
    assert len(prompt) > 100
