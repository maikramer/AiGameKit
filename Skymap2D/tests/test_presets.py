"""Testes para skymap2d.presets."""

import pytest

from skymap2d.presets import (
    SKYMAP_PRESETS,
    get_preset,
    get_preset_params,
    get_preset_prompt,
    list_presets,
)


class TestPresets:
    def test_list_presets_not_empty(self):
        presets = list_presets()
        assert len(presets) >= 10

    def test_all_presets_have_required_keys(self):
        for name, preset in SKYMAP_PRESETS.items():
            assert "prompt" in preset, f"{name} sem prompt"
            assert "negative_prompt" in preset, f"{name} sem negative_prompt"
            assert "guidance_scale" in preset, f"{name} sem guidance_scale"
            assert "num_inference_steps" in preset, f"{name} sem num_inference_steps"

    def test_all_presets_have_2_1_ratio(self):
        pytest.skip("Presets não incluem width/height — resolução vem de defaults/CLI")

    def test_get_preset_existing(self):
        preset = get_preset("Sunset")
        assert preset is not None
        assert "prompt" in preset

    def test_get_preset_nonexistent(self):
        preset = get_preset("NonExistent")
        assert preset is None

    def test_get_preset_prompt(self):
        prompt = get_preset_prompt("Night Sky")
        assert prompt is not None
        assert "night" in prompt.lower() or "star" in prompt.lower()

    def test_get_preset_params_excludes_prompt(self):
        params = get_preset_params("Storm")
        assert params is not None
        assert "prompt" not in params
        assert "guidance_scale" in params

    def test_environment_presets_exist(self):
        names = list_presets()
        for expected in ["Sunset", "Night Sky", "Space", "Dawn", "Fantasy"]:
            assert expected in names, f"Preset '{expected}' em falta"
