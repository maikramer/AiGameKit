"""Testes para texture2d.presets."""

from texture2d.presets import (
    TEXTURE_PRESETS,
    get_preset,
    get_preset_params,
    get_preset_prompt,
    list_presets,
)


class TestPresets:
    def test_list_presets_returns_thirteen(self):
        presets = list_presets()
        assert len(presets) == 13

    def test_all_presets_have_required_keys(self):
        for name, preset in TEXTURE_PRESETS.items():
            assert "prompt" in preset, f"{name} sem prompt"
            assert "negative_prompt" in preset, f"{name} sem negative_prompt"
            assert "guidance_scale" in preset, f"{name} sem guidance_scale"
            assert "num_inference_steps" in preset, f"{name} sem num_inference_steps"

    def test_get_preset_existing(self):
        preset = get_preset("Stone")
        assert preset is not None
        assert "prompt" in preset

    def test_get_preset_nonexistent(self):
        preset = get_preset("NonExistent")
        assert preset is None

    def test_get_preset_prompt(self):
        prompt = get_preset_prompt("Wood")
        assert prompt is not None
        assert "wood" in prompt.lower()

    def test_get_preset_params_excludes_prompt(self):
        params = get_preset_params("Metal")
        assert params is not None
        assert "prompt" not in params
        assert "guidance_scale" in params

    def test_game_dev_presets_exist(self):
        names = list_presets()
        for expected in ["Grass", "Sand", "Dirt", "Gravel"]:
            assert expected in names, f"Preset game-dev '{expected}' em falta"
