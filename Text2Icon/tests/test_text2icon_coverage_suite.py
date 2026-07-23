"""Suite de cobertura Text2Icon (utils, ums_payload, generator, CLI) sem GPU/HF."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

# --- validate_prompt ---


@pytest.mark.parametrize(
    "prompt,ok",
    [
        ("valid prompt", True),
        ("  padded  ", True),
        ("", False),
        ("   ", False),
        ("\n\t", False),
    ],
)
def test_validate_prompt_empty_and_trim(prompt: str, ok: bool) -> None:
    from text2icon.utils import validate_prompt

    valid, err = validate_prompt(prompt)
    assert valid is ok
    if not ok:
        assert err is not None


def test_validate_prompt_max_length_default() -> None:
    from text2icon.utils import validate_prompt

    valid, err = validate_prompt("x" * 1000)
    assert valid is True and err is None


def test_validate_prompt_exceeds_max_length() -> None:
    from text2icon.utils import validate_prompt

    valid, err = validate_prompt("x" * 1001)
    assert valid is False
    assert err is not None and "1000" in err


def test_validate_prompt_custom_max_length() -> None:
    from text2icon.utils import validate_prompt

    valid, err = validate_prompt("abc", max_length=2)
    assert valid is False


# --- validate_dimensions ---


@pytest.mark.parametrize(
    "width,height,ok",
    [
        (512, 512, True),
        (256, 2048, True),
        (1024, 768, True),
        (255, 512, False),
        (512, 2049, False),
        (512, 513, False),
        (518, 512, False),
    ],
)
def test_validate_dimensions_bounds_and_multiple_of_8(width: int, height: int, ok: bool) -> None:
    from text2icon.utils import validate_dimensions

    valid, err = validate_dimensions(width, height)
    assert valid is ok
    if not ok:
        assert err is not None


def test_validate_dimensions_error_message_width() -> None:
    from text2icon.utils import validate_dimensions

    _, err = validate_dimensions(100, 512)
    assert err is not None and "Largura" in err


def test_validate_dimensions_error_message_height() -> None:
    from text2icon.utils import validate_dimensions

    _, err = validate_dimensions(512, 100)
    assert err is not None and "Altura" in err


def test_validate_dimensions_error_message_mod8() -> None:
    from text2icon.utils import validate_dimensions

    _, err = validate_dimensions(512, 514)
    assert err is not None and "8" in err


# --- validate_params ---


@pytest.mark.parametrize(
    "guidance,ok",
    [
        (1.0, True),
        (20.0, True),
        (4.5, True),
        (0.9, False),
        (20.1, False),
    ],
)
def test_validate_params_guidance_range(guidance: float, ok: bool) -> None:
    from text2icon.utils import validate_params

    valid, _ = validate_params({"guidance_scale": guidance, "num_inference_steps": 2, "width": 512, "height": 512})
    assert valid is ok


@pytest.mark.parametrize(
    "steps,ok",
    [
        (1, True),
        (100, True),
        (0, False),
        (101, False),
    ],
)
def test_validate_params_steps_range(steps: int, ok: bool) -> None:
    from text2icon.utils import validate_params

    valid, _ = validate_params({"guidance_scale": 4.5, "num_inference_steps": steps, "width": 512, "height": 512})
    assert valid is ok


def test_validate_params_defaults_ok() -> None:
    from text2icon.utils import validate_params

    valid, err = validate_params({})
    assert valid is True and err is None


def test_validate_params_propagates_bad_dimensions() -> None:
    from text2icon.utils import validate_params

    valid, err = validate_params({"guidance_scale": 4.5, "num_inference_steps": 2, "width": 300, "height": 512})
    assert valid is False
    assert err is not None


# --- format_timestamp ---


def test_format_timestamp_epoch_zero() -> None:
    from text2icon.utils import format_timestamp

    s = format_timestamp(0.0)
    assert len(s) == 19
    assert s[4] == "-" and s[7] == "-"


def test_format_timestamp_known_value() -> None:
    from text2icon.utils import format_timestamp

    # 2020-01-01 00:00:00 UTC — depende do TZ local; só verifica formato.
    s = format_timestamp(1577836800.0)
    assert s.startswith("20")


def test_format_timestamp_fraction_ignored() -> None:
    from text2icon.utils import format_timestamp

    a = format_timestamp(1000000000.0)
    b = format_timestamp(1000000000.9)
    assert a == b


# --- build_generate_request ---


def test_icon_ums_defaults_size() -> None:
    from text2icon.ums_payload import build_generate_request

    req = build_generate_request(prompt="icon", output="/tmp/i.png")
    assert req["width"] == 512
    assert req["height"] == 512


def test_icon_ums_defaults_steps_guidance() -> None:
    from text2icon.ums_payload import build_generate_request

    req = build_generate_request(prompt="icon", output="/tmp/i.png")
    assert req["steps"] == 2
    assert req["guidance"] == 4.5


def test_icon_ums_transparent_default_false() -> None:
    from text2icon.ums_payload import build_generate_request

    req = build_generate_request(prompt="icon", output="/tmp/i.png")
    assert req["transparent"] is False


def test_icon_ums_transparent_true() -> None:
    from text2icon.ums_payload import build_generate_request

    req = build_generate_request(prompt="icon", output="/tmp/i.png", transparent=True)
    assert req["transparent"] is True


def test_icon_ums_negative_prompt_optional() -> None:
    from text2icon.ums_payload import build_generate_request

    req = build_generate_request(prompt="icon", output="/tmp/i.png", negative_prompt="blur")
    assert req["negative_prompt"] == "blur"


def test_icon_ums_negative_prompt_omitted() -> None:
    from text2icon.ums_payload import build_generate_request

    req = build_generate_request(prompt="icon", output="/tmp/i.png")
    assert "negative_prompt" not in req


def test_icon_ums_transformer_quant_in_payload() -> None:
    from text2icon.ums_payload import build_generate_request

    req = build_generate_request(prompt="icon", output="/tmp/i.png", transformer_quant_preset="sdnq-int4")
    assert req["transformer_quant_preset"] == "sdnq-int4"
    assert req.get("sdnq_preset") == "sdnq-int4"


def test_icon_ums_transformer_quant_auto_no_sdnq_peak() -> None:
    from text2icon.ums_payload import build_generate_request

    req = build_generate_request(prompt="icon", output="/tmp/i.png", transformer_quant_preset="auto")
    assert req["transformer_quant_preset"] == "auto"
    assert "sdnq_preset" not in req


def test_icon_ums_model_id_set() -> None:
    from text2icon.ums_payload import build_generate_request

    mid = "Efficient-Large-Model/Sana_600M_512px_diffusers"
    req = build_generate_request(prompt="icon", output="/tmp/i.png", model_id=mid)
    assert req["model_id"] == mid


def test_icon_ums_gpu_ids_list() -> None:
    from text2icon.ums_payload import build_generate_request

    req = build_generate_request(prompt="icon", output="/tmp/i.png", gpu_ids=[1])
    assert req["gpu_ids"] == [1]


def test_icon_ums_gpu_ids_csv() -> None:
    from text2icon.ums_payload import build_generate_request

    req = build_generate_request(prompt="icon", output="/tmp/i.png", gpu_ids="0,2")
    assert req["gpu_ids"] == [0, 2]


def test_icon_ums_memory_efficient_explicit_true() -> None:
    from text2icon.ums_payload import build_generate_request

    req = build_generate_request(prompt="icon", output="/tmp/i.png", memory_efficient=True)
    assert req["memory_efficient"] is True


def test_icon_ums_memory_efficient_follows_quant_when_unset() -> None:
    from text2icon.ums_payload import build_generate_request

    req = build_generate_request(
        prompt="icon", output="/tmp/i.png", transformer_quant_preset="sdnq-uint8", memory_efficient=None
    )
    assert req["memory_efficient"] is True


def test_icon_ums_extra_merged() -> None:
    from text2icon.ums_payload import build_generate_request

    req = build_generate_request(prompt="icon", output="/tmp/i.png", extra={"batch_id": "b1"})
    assert req["batch_id"] == "b1"


def test_icon_ums_seed_and_coercion() -> None:
    from text2icon.ums_payload import build_generate_request

    req = build_generate_request(prompt=99, output=88, seed=7, width="256", height="256")
    assert req["prompt"] == "99"
    assert req["output"] == "88"
    assert req["seed"] == 7
    assert req["width"] == 256


# --- augment_prompt_for_icon ---


@pytest.mark.parametrize(
    "prompt",
    [
        "Health icon for game",
        "App Icon shiny",
        "Company LOGO",
        "Guild emblem gold",
        "Quest badge",
        "UI glyph",
    ],
)
def test_augment_skips_when_keyword_present(prompt: str) -> None:
    from text2icon.generator import augment_prompt_for_icon

    assert augment_prompt_for_icon(prompt) == prompt


def test_augment_adds_instructions_for_plain_subject() -> None:
    from text2icon.generator import BASE_ICON_INSTRUCTIONS, augment_prompt_for_icon

    out = augment_prompt_for_icon("red potion")
    assert out.startswith(BASE_ICON_INSTRUCTIONS.split(",")[0])
    assert out.endswith("red potion")


def test_augment_glyph_case_insensitive() -> None:
    from text2icon.generator import augment_prompt_for_icon

    p = "Magic GLYPH rune"
    assert augment_prompt_for_icon(p) == p


# --- default_model_id ---


def test_default_model_id_without_env() -> None:
    import os

    from text2icon.generator import DEFAULT_TRANSFORMER_ID, default_model_id

    prev = os.environ.pop("TEXT2ICON_MODEL_ID", None)
    try:
        assert default_model_id() == DEFAULT_TRANSFORMER_ID
    finally:
        if prev is not None:
            os.environ["TEXT2ICON_MODEL_ID"] = prev


def test_default_model_id_respects_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from text2icon.generator import default_model_id

    monkeypatch.setenv("TEXT2ICON_MODEL_ID", "org/custom-sana")
    assert default_model_id() == "org/custom-sana"


# --- CLI smoke ---


def test_coverage_cli_root_help() -> None:
    from text2icon.cli import cli

    r = CliRunner().invoke(cli, ["--help"])
    assert r.exit_code == 0


def test_coverage_cli_generate_help_flags() -> None:
    from text2icon.cli import cli

    r = CliRunner().invoke(cli, ["generate", "--help"])
    assert r.exit_code == 0
    assert "--transparent" in r.output


def test_coverage_cli_batch_help() -> None:
    from text2icon.cli import cli

    r = CliRunner().invoke(cli, ["batch", "--help"])
    assert r.exit_code == 0


def test_coverage_cli_info_runs() -> None:
    from text2icon.cli import cli

    r = CliRunner().invoke(cli, ["info"])
    assert r.exit_code == 0


def test_coverage_cli_generate_missing_arg() -> None:
    from text2icon.cli import cli

    r = CliRunner().invoke(cli, ["generate"])
    assert r.exit_code != 0


def test_coverage_cli_version_string() -> None:
    from text2icon.cli import cli

    r = CliRunner().invoke(cli, ["--version"])
    assert r.exit_code == 0
    assert "text2icon" in r.output.lower() or "0.1.0" in r.output


def test_validate_prompt_unicode_ok() -> None:
    from text2icon.utils import validate_prompt

    valid, err = validate_prompt("ícone de poção 🧪")
    assert valid is True and err is None


def test_validate_dimensions_min_edge_256() -> None:
    from text2icon.utils import validate_dimensions

    valid, _ = validate_dimensions(256, 256)
    assert valid is True


def test_validate_dimensions_max_edge_2048() -> None:
    from text2icon.utils import validate_dimensions

    valid, _ = validate_dimensions(2048, 2048)
    assert valid is True


def test_validate_params_steps_at_boundary_100() -> None:
    from text2icon.utils import validate_params

    valid, _ = validate_params({"guidance_scale": 4.5, "num_inference_steps": 100, "width": 512, "height": 512})
    assert valid is True


def test_icon_ums_memory_efficient_false_overrides_quant() -> None:
    from text2icon.ums_payload import build_generate_request

    req = build_generate_request(
        prompt="icon",
        output="/tmp/i.png",
        transformer_quant_preset="sdnq-int4",
        memory_efficient=False,
    )
    assert req["memory_efficient"] is False


def test_icon_ums_empty_extra_ignored() -> None:
    from text2icon.ums_payload import build_generate_request

    req = build_generate_request(prompt="icon", output="/tmp/i.png", extra={})
    assert "batch_id" not in req


def test_icon_ums_transformer_quant_empty_string_no_peak_quant() -> None:
    from text2icon.ums_payload import build_generate_request

    req = build_generate_request(prompt="icon", output="/tmp/i.png", transformer_quant_preset="")
    assert "sdnq_preset" not in req


def test_augment_whitespace_only_empty() -> None:
    from text2icon.generator import augment_prompt_for_icon

    assert augment_prompt_for_icon("  \n  ") == ""


def test_augment_app_icon_phrase_skips() -> None:
    from text2icon.generator import augment_prompt_for_icon

    p = "Minimal app icon flat"
    assert augment_prompt_for_icon(p) == p


def test_default_model_id_standard_constant() -> None:
    import os

    from text2icon.generator import STANDARD_TRANSFORMER_ID, default_model_id

    os.environ.pop("TEXT2ICON_MODEL_ID", None)
    assert default_model_id() == STANDARD_TRANSFORMER_ID


def test_coverage_cli_skill_install_help() -> None:
    from text2icon.cli import cli

    r = CliRunner().invoke(cli, ["skill", "install", "--help"])
    assert r.exit_code == 0


def test_validate_prompt_single_char_ok() -> None:
    from text2icon.utils import validate_prompt

    valid, err = validate_prompt("a")
    assert valid is True and err is None


def test_validate_params_guidance_at_lower_bound() -> None:
    from text2icon.utils import validate_params

    valid, err = validate_params({"guidance_scale": 1.0})
    assert valid is True and err is None


def test_validate_params_guidance_at_upper_bound() -> None:
    from text2icon.utils import validate_params

    valid, err = validate_params({"guidance_scale": 20.0})
    assert valid is True and err is None


def test_icon_ums_prompt_output_strings() -> None:
    from text2icon.ums_payload import build_generate_request

    req = build_generate_request(prompt="p", output="out.png")
    assert req["prompt"] == "p"
    assert req["output"] == "out.png"


def test_icon_ums_seed_none_default() -> None:
    from text2icon.ums_payload import build_generate_request

    req = build_generate_request(prompt="p", output="out.png")
    assert req["seed"] is None
