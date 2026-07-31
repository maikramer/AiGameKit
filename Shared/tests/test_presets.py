"""Testes dos accessors de presets partilhados (``gamedev_shared.presets``).

As tools 2D (Texture2D/Skymap2D/Text2Sound) usam estes accessors sobre os
seus dicts de conteúdo; aqui testa-se o contrato comum.
"""

from __future__ import annotations

import pytest

from gamedev_shared.presets import (
    get_preset,
    get_preset_ci,
    get_preset_params,
    get_preset_prompt,
    list_presets,
)

# Chaves minúsculas: convenção do Text2Sound (get_preset_ci normaliza o nome).
DEMO_PRESETS: dict[str, dict[str, object]] = {
    "wood": {"prompt": "seamless wood", "guidance_scale": 7.5, "num_inference_steps": 50},
    "metal": {"prompt": "seamless metal", "guidance_scale": 8.0, "num_inference_steps": 60},
    "stone": {"prompt": "seamless stone", "guidance_scale": 7.5, "num_inference_steps": 50},
}


class TestGetPreset:
    def test_existing(self) -> None:
        assert get_preset(DEMO_PRESETS, "wood") is DEMO_PRESETS["wood"]

    def test_missing_returns_none(self) -> None:
        assert get_preset(DEMO_PRESETS, "Nope") is None

    def test_empty_dict(self) -> None:
        assert get_preset({}, "x") is None


class TestListPresets:
    def test_insertion_order(self) -> None:
        assert list_presets(DEMO_PRESETS) == ["wood", "metal", "stone"]

    def test_sorted(self) -> None:
        assert list_presets(DEMO_PRESETS, sorted_names=True) == ["metal", "stone", "wood"]

    def test_empty(self) -> None:
        assert list_presets({}) == []


class TestGetPresetPrompt:
    def test_existing(self) -> None:
        assert get_preset_prompt(DEMO_PRESETS, "wood") == "seamless wood"

    def test_missing(self) -> None:
        assert get_preset_prompt(DEMO_PRESETS, "Nope") is None

    def test_custom_key(self) -> None:
        assert get_preset_prompt(DEMO_PRESETS, "wood", key="guidance_scale") == 7.5


class TestGetPresetParams:
    def test_excludes_prompt_by_default(self) -> None:
        params = get_preset_params(DEMO_PRESETS, "metal")
        assert params == {"guidance_scale": 8.0, "num_inference_steps": 60}
        assert "prompt" not in params

    def test_keeps_prompt_when_asked(self) -> None:
        params = get_preset_params(DEMO_PRESETS, "metal", exclude_prompt=False)
        assert params["prompt"] == "seamless metal"

    def test_does_not_mutate_source(self) -> None:
        get_preset_params(DEMO_PRESETS, "wood")
        assert "prompt" in DEMO_PRESETS["wood"]

    def test_missing(self) -> None:
        assert get_preset_params(DEMO_PRESETS, "Nope") is None


class TestGetPresetCi:
    def test_case_insensitive(self) -> None:
        assert get_preset_ci(DEMO_PRESETS, "wood") is DEMO_PRESETS["wood"]

    def test_normalized_separators(self) -> None:
        assert get_preset_ci(DEMO_PRESETS, "WOOD") is DEMO_PRESETS["wood"]

    def test_unknown_raises_keyerror(self) -> None:
        with pytest.raises(KeyError):
            get_preset_ci(DEMO_PRESETS, "dirt")
