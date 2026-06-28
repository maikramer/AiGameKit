"""Testes extra Skymap2D: generator, presets, CLI."""

from __future__ import annotations

import pytest

from skymap2d.generator import (
    BASE_EQUIRECTANGULAR_INSTRUCTIONS,
    augment_prompt_for_equirectangular,
    default_model_id,
    merge_negative_prompt,
)
from skymap2d.presets import SKYMAP_PRESETS, list_presets


def test_default_model_id_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SKYMAP2D_MODEL_ID", "org/my-model")
    assert default_model_id() == "org/my-model"


def test_augment_empty() -> None:
    assert augment_prompt_for_equirectangular("") == ""


def test_augment_skips_equirectangular() -> None:
    p = "equirectangular sunset"
    assert augment_prompt_for_equirectangular(p) == p


def test_augment_skips_panorama() -> None:
    p = "A panoramic view"
    assert augment_prompt_for_equirectangular(p) == p


def test_augment_skips_360() -> None:
    p = "360 degree view"
    assert augment_prompt_for_equirectangular(p) == p


def test_augment_skips_hdri() -> None:
    p = "hdri environment"
    assert augment_prompt_for_equirectangular(p) == p


def test_augment_adds_base() -> None:
    out = augment_prompt_for_equirectangular("alpine peaks")
    assert BASE_EQUIRECTANGULAR_INSTRUCTIONS.split(",")[0] in out
    assert "alpine" in out


def test_merge_negative_preset_only() -> None:
    assert merge_negative_prompt("clouds", "") == "clouds"


def test_merge_negative_user_only() -> None:
    assert merge_negative_prompt("", "people") == "people"


def test_merge_negative_combined() -> None:
    m = merge_negative_prompt("a", "b")
    assert "a" in m and "b" in m


def test_merge_negative_dedup_user_contains_preset() -> None:
    assert merge_negative_prompt("blur", "no blur") == "no blur"


def test_list_presets_matches_dict() -> None:
    assert set(list_presets()) == set(SKYMAP_PRESETS.keys())


def test_presets_non_empty() -> None:
    assert len(SKYMAP_PRESETS) >= 1


def test_cli_root_help() -> None:
    from click.testing import CliRunner

    from skymap2d.cli import cli

    r = CliRunner().invoke(cli, ["--help"])
    assert r.exit_code == 0


def test_cli_generate_help() -> None:
    from click.testing import CliRunner

    from skymap2d.cli import cli

    r = CliRunner().invoke(cli, ["generate", "--help"])
    assert r.exit_code == 0


def test_cli_batch_help() -> None:
    from click.testing import CliRunner

    from skymap2d.cli import cli

    r = CliRunner().invoke(cli, ["batch", "--help"])
    assert r.exit_code == 0


def test_cli_info_help() -> None:
    from click.testing import CliRunner

    from skymap2d.cli import cli

    r = CliRunner().invoke(cli, ["info", "--help"])
    assert r.exit_code == 0


def test_cli_skill_install_help() -> None:
    from click.testing import CliRunner

    from skymap2d.cli import cli

    r = CliRunner().invoke(cli, ["skill", "install", "--help"])
    assert r.exit_code == 0


def test_version_cli() -> None:
    from click.testing import CliRunner

    from skymap2d.cli import cli

    r = CliRunner().invoke(cli, ["--version"])
    assert r.exit_code == 0
    assert "0.1.0" in r.output


def test_validate_params_integration() -> None:
    from skymap2d.utils import validate_params

    ok, _ = validate_params({"guidance_scale": 6.0, "num_inference_steps": 40, "width": 2048, "height": 1024})
    assert ok is True
