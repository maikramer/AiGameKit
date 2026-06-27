"""Testes extra Texture2D: generator, utils, presets."""

from __future__ import annotations

from pathlib import Path

import pytest

from texture2d.generator import (
    BASE_TEXTURE_INSTRUCTIONS,
    augment_prompt_for_seamless,
    default_model_id,
    merge_negative_prompt,
)
from texture2d.presets import TEXTURE_PRESETS, get_preset_prompt, list_presets
from texture2d.utils import (
    ensure_directory,
    format_bytes,
    format_timestamp,
    generate_seed,
    validate_dimensions,
    validate_params,
    validate_prompt,
)


def test_default_model_id_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEXTURE2D_MODEL_ID", "custom/model")
    assert default_model_id() == "custom/model"


def test_augment_empty_returns_empty() -> None:
    assert augment_prompt_for_seamless("") == ""
    assert augment_prompt_for_seamless("   ") == ""


def test_augment_skips_when_seamless_mentioned() -> None:
    p = "A seamless stone wall"
    assert augment_prompt_for_seamless(p) == p


def test_augment_skips_tileable() -> None:
    p = "Tileable wood pattern"
    assert augment_prompt_for_seamless(p) == p


def test_augment_skips_repeatable() -> None:
    p = "Repeatable fabric"
    assert augment_prompt_for_seamless(p) == p


def test_augment_adds_base_instructions() -> None:
    out = augment_prompt_for_seamless("rust metal")
    assert BASE_TEXTURE_INSTRUCTIONS.split(",")[0].strip() in out
    assert "rust metal" in out


def test_merge_negative_preset_only() -> None:
    assert merge_negative_prompt("blur", "") == "blur"


def test_merge_negative_user_only() -> None:
    assert merge_negative_prompt("", "noise") == "noise"


def test_merge_negative_both() -> None:
    m = merge_negative_prompt("a", "b")
    assert "a" in m and "b" in m


def test_merge_negative_subset_dedup() -> None:
    assert merge_negative_prompt("blur", "no blur please") == "no blur please"


def test_validate_params_width_height_invalid() -> None:
    ok, err = validate_params({"guidance_scale": 7.5, "num_inference_steps": 50, "width": 100, "height": 1024})
    assert ok is False
    assert err is not None


def test_validate_dimensions_square_1024() -> None:
    assert validate_dimensions(1024, 1024)[0] is True


def test_format_timestamp_shape() -> None:
    s = format_timestamp(1_700_000_000.0)
    assert len(s) == 19


def test_ensure_directory_nested(tmp_path: Path) -> None:
    d = tmp_path / "u" / "v"
    ensure_directory(d)
    assert d.is_dir()


def test_format_bytes_tb() -> None:
    s = format_bytes(1024**4)
    assert "TB" in s


def test_format_bytes_pb() -> None:
    s = format_bytes(1024**5)
    assert "PB" in s


def test_generate_seed_range() -> None:
    for _ in range(30):
        s = generate_seed()
        assert 0 <= s < 2**32


def test_list_presets_contains_keys() -> None:
    names = list_presets()
    assert len(names) == len(TEXTURE_PRESETS)
    assert set(names) == set(TEXTURE_PRESETS.keys())


def test_get_preset_prompt_missing() -> None:
    assert get_preset_prompt("___nope___") is None


def test_cli_help() -> None:
    from click.testing import CliRunner

    from texture2d.cli import cli

    r = CliRunner().invoke(cli, ["--help"])
    assert r.exit_code == 0


def test_cli_generate_help() -> None:
    from click.testing import CliRunner

    from texture2d.cli import cli

    r = CliRunner().invoke(cli, ["generate", "--help"])
    assert r.exit_code == 0


def test_cli_presets_help() -> None:
    from click.testing import CliRunner

    from texture2d.cli import cli

    r = CliRunner().invoke(cli, ["presets", "--help"])
    assert r.exit_code == 0


def test_validate_prompt_max_length_ok() -> None:
    ok, err = validate_prompt("x" * 100, max_length=500)
    assert ok is True and err is None
