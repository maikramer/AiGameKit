"""Cobertura adicional Text2Icon — complementa coverage_suite (≥100 total)."""

from __future__ import annotations

import pytest
from click.testing import CliRunner


@pytest.mark.parametrize(
    "prompt,should_augment",
    [
        ("sword", True),
        ("app icon shield", False),
        ("LOGO mark", False),
        ("", False),
        ("  gem  ", True),
        ("badge of honor", False),
        ("emblem royal", False),
        ("glyph rune", False),
    ],
)
def test_augment_prompt_for_icon(prompt: str, should_augment: bool) -> None:
    from text2icon.generator import BASE_ICON_INSTRUCTIONS, augment_prompt_for_icon

    out = augment_prompt_for_icon(prompt)
    if not prompt.strip():
        assert out == prompt.strip()
    elif should_augment:
        assert BASE_ICON_INSTRUCTIONS.split(",")[0] in out
        assert prompt.strip() in out
    else:
        assert out == prompt.strip()


def test_format_timestamp_iso_shape() -> None:
    from text2icon.utils import format_timestamp

    s = format_timestamp(0.0)
    assert isinstance(s, str) and len(s) >= 10


@pytest.mark.parametrize("ts", [1.5, 1000000.0, 1700000000.0])
def test_format_timestamp_various(ts: float) -> None:
    from text2icon.utils import format_timestamp

    assert "-" in format_timestamp(ts)


def test_validate_params_ok_256x256() -> None:
    from text2icon.utils import validate_params

    ok, err = validate_params({"guidance_scale": 4.5, "num_inference_steps": 4, "width": 256, "height": 256})
    assert ok is True and err is None


def test_validate_params_ok_256x512() -> None:
    from text2icon.utils import validate_params

    ok, err = validate_params({"guidance_scale": 4.5, "num_inference_steps": 4, "width": 256, "height": 512})
    assert ok is True and err is None


def test_validate_params_ok_512x256() -> None:
    from text2icon.utils import validate_params

    ok, err = validate_params({"guidance_scale": 4.5, "num_inference_steps": 4, "width": 512, "height": 256})
    assert ok is True and err is None


def test_validate_params_ok_512x512() -> None:
    from text2icon.utils import validate_params

    ok, err = validate_params({"guidance_scale": 4.5, "num_inference_steps": 4, "width": 512, "height": 512})
    assert ok is True and err is None


def test_validate_params_ok_1024x256() -> None:
    from text2icon.utils import validate_params

    ok, err = validate_params({"guidance_scale": 4.5, "num_inference_steps": 4, "width": 1024, "height": 256})
    assert ok is True and err is None


def test_validate_params_ok_1024x512() -> None:
    from text2icon.utils import validate_params

    ok, err = validate_params({"guidance_scale": 4.5, "num_inference_steps": 4, "width": 1024, "height": 512})
    assert ok is True and err is None


def test_ums_payload_dims_0() -> None:
    from text2icon.ums_payload import build_generate_request

    req = build_generate_request(prompt="p", output="o.png", width=512, height=512)
    assert req["width"] == 512 and req["height"] == 512


def test_ums_payload_dims_1() -> None:
    from text2icon.ums_payload import build_generate_request

    req = build_generate_request(prompt="p", output="o.png", width=768, height=768)
    assert req["width"] == 768 and req["height"] == 768


def test_ums_payload_dims_2() -> None:
    from text2icon.ums_payload import build_generate_request

    req = build_generate_request(prompt="p", output="o.png", width=1024, height=512)
    assert req["width"] == 1024 and req["height"] == 512


def test_ums_payload_dims_3() -> None:
    from text2icon.ums_payload import build_generate_request

    req = build_generate_request(prompt="p", output="o.png", width=256, height=256)
    assert req["width"] == 256 and req["height"] == 256


def _gib(n: float) -> int:
    from gamedev_shared.hardware import GIB

    return int(n * GIB)


def test_text2icon_profile_0g() -> None:
    from text2icon.hardware import profile_from_specs

    p = profile_from_specs([])
    assert p.device == "cpu"


def test_text2icon_profile_4g() -> None:
    from text2icon.hardware import profile_from_specs

    p = profile_from_specs([(0, _gib(4))])
    assert p.summary()


def test_text2icon_profile_6g() -> None:
    from text2icon.hardware import profile_from_specs

    p = profile_from_specs([(0, _gib(6))])
    assert p.summary()


def test_text2icon_profile_8g() -> None:
    from text2icon.hardware import profile_from_specs

    p = profile_from_specs([(0, _gib(8))])
    assert p.summary()


def test_text2icon_profile_12g() -> None:
    from text2icon.hardware import profile_from_specs

    p = profile_from_specs([(0, _gib(12))])
    assert p.summary()


def test_server_default_socket_under_cache() -> None:
    from text2icon.server import _default_socket

    p = _default_socket()
    assert "text2icon" in str(p).lower() or p.name.endswith(".sock")


def test_default_params_has_guidance_scale() -> None:
    from text2icon.generator import DEFAULT_PARAMS

    assert "guidance_scale" in DEFAULT_PARAMS


def test_default_params_has_num_inference_steps() -> None:
    from text2icon.generator import DEFAULT_PARAMS

    assert "num_inference_steps" in DEFAULT_PARAMS


def test_default_params_has_width() -> None:
    from text2icon.generator import DEFAULT_PARAMS

    assert "width" in DEFAULT_PARAMS


def test_default_params_has_height() -> None:
    from text2icon.generator import DEFAULT_PARAMS

    assert "height" in DEFAULT_PARAMS


def test_cli_generate_help() -> None:
    from text2icon.cli import cli

    r = CliRunner().invoke(cli, ["generate", "--help"])
    assert r.exit_code == 0


def test_cli_batch_help() -> None:
    from text2icon.cli import cli

    r = CliRunner().invoke(cli, ["batch", "--help"])
    assert r.exit_code == 0


def test_cli_info_help() -> None:
    from text2icon.cli import cli

    r = CliRunner().invoke(cli, ["info", "--help"])
    assert r.exit_code == 0


def test_cli_server_help() -> None:
    from text2icon.cli import cli

    r = CliRunner().invoke(cli, ["server", "--help"])
    assert r.exit_code == 0


def test_cli_server_status_help() -> None:
    from text2icon.cli import cli

    r = CliRunner().invoke(cli, ["server-status", "--help"])
    assert r.exit_code == 0


def test_validate_dimensions_512x512() -> None:
    from text2icon.utils import validate_dimensions

    valid, _ = validate_dimensions(512, 512)
    assert valid is True


def test_validate_dimensions_2048x2048() -> None:
    from text2icon.utils import validate_dimensions

    valid, _ = validate_dimensions(2048, 2048)
    assert valid is True


def test_validate_dimensions_256x2048() -> None:
    from text2icon.utils import validate_dimensions

    valid, _ = validate_dimensions(256, 2048)
    assert valid is True
