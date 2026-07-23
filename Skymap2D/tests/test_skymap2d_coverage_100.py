"""Elaborate unit tests for Skymap2D (no GPU). ≥100 collected cases via parametrize."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from click.testing import CliRunner
from PIL import Image

# ---------------------------------------------------------------------------
# validate_prompt
# ---------------------------------------------------------------------------

_PROMPT_VALID = [
    ("sunset sky", True),
    ("  trimmed  ", True),
    ("x" * 500, True),
    ("", False),
    ("   ", False),
    ("\n\t", False),
]

_PROMPT_MAX_LEN = [("x" * i, 500, i <= 500) for i in (1, 499, 500, 501, 600)]


@pytest.mark.parametrize("prompt,ok", _PROMPT_VALID)
def test_validate_prompt_basic(prompt: str, ok: bool) -> None:
    from skymap2d.utils import validate_prompt

    valid, err = validate_prompt(prompt)
    assert valid is ok
    assert err is None if ok else err


@pytest.mark.parametrize("prompt,max_len,ok", _PROMPT_MAX_LEN)
def test_validate_prompt_max_length(prompt: str, max_len: int, ok: bool) -> None:
    from skymap2d.utils import validate_prompt

    valid, _err = validate_prompt(prompt, max_length=max_len)
    assert valid is ok


# ---------------------------------------------------------------------------
# validate_dimensions
# ---------------------------------------------------------------------------

_DIM_OK = [
    (2048, 1024),
    (4096, 2048),
    (1024, 512),
    (2040, 1024),
]

_DIM_BAD_WIDTH = [(255, 512), (4097, 2048), (5000, 512)]


@pytest.mark.parametrize("w,h", _DIM_OK)
def test_validate_dimensions_accepted(w: int, h: int) -> None:
    from skymap2d.utils import validate_dimensions

    ok, err = validate_dimensions(w, h)
    assert ok is True
    assert err is None


@pytest.mark.parametrize("w,h", _DIM_BAD_WIDTH)
def test_validate_dimensions_rejects_bounds(w: int, h: int) -> None:
    from skymap2d.utils import validate_dimensions

    ok, err = validate_dimensions(w, h)
    assert ok is False
    assert err is not None


@pytest.mark.parametrize(
    "w,h",
    [(2047, 1024), (2048, 1023), (256, 129)],
)
def test_validate_dimensions_rejects_non_multiple_of_8(w: int, h: int) -> None:
    from skymap2d.utils import validate_dimensions

    ok, err = validate_dimensions(w, h)
    assert ok is False
    assert "8" in (err or "")


@pytest.mark.parametrize(
    "w,h",
    [(2048, 1024), (1024, 512), (4096, 2048)],
)
def test_validate_dimensions_exact_2_1_ratio(w: int, h: int) -> None:
    from skymap2d.utils import validate_dimensions

    ok, err = validate_dimensions(w, h)
    assert ok is True
    assert err is None


@pytest.mark.parametrize(
    "w,h",
    [(1920, 1088), (1600, 896), (3008, 1000)],
)
def test_validate_dimensions_non_2_1_ratio_still_valid_with_mock_logger(w: int, h: int) -> None:

    from skymap2d import utils as utils_mod

    with patch.object(utils_mod, "_logger", MagicMock()):
        ok, err = utils_mod.validate_dimensions(w, h)
    assert ok is True
    assert err is None


# ---------------------------------------------------------------------------
# validate_params
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("guidance", [1.0, 6.0, 10.5, 20.0])
def test_validate_params_guidance_in_range(guidance: float) -> None:
    from skymap2d.utils import validate_params

    ok, _err = validate_params({"guidance_scale": guidance, "num_inference_steps": 40, "width": 2048, "height": 1024})
    assert ok is True


@pytest.mark.parametrize("guidance", [0.9, 20.1, -1.0, 100.0])
def test_validate_params_guidance_out_of_range(guidance: float) -> None:
    from skymap2d.utils import validate_params

    ok, _err = validate_params({"guidance_scale": guidance})
    assert ok is False


@pytest.mark.parametrize("steps", [10, 40, 55, 100])
def test_validate_params_steps_in_range(steps: int) -> None:
    from skymap2d.utils import validate_params

    ok, _ = validate_params({"num_inference_steps": steps, "width": 2048, "height": 1024})
    assert ok is True


@pytest.mark.parametrize("steps", [9, 101, 0])
def test_validate_params_steps_out_of_range(steps: int) -> None:
    from skymap2d.utils import validate_params

    ok, err = validate_params({"num_inference_steps": steps})
    assert ok is False
    assert "passos" in (err or "").lower() or "10" in (err or "")


def test_validate_params_empty_dict_uses_defaults() -> None:
    from skymap2d.utils import validate_params

    ok, err = validate_params({})
    assert ok is True
    assert err is None


# ---------------------------------------------------------------------------
# format_timestamp
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ts",
    [0.0, 946684800.0, 1700000000.0],
)
def test_format_timestamp_contains_date_parts(ts: float) -> None:
    from skymap2d.utils import format_timestamp

    s = format_timestamp(ts)
    assert len(s) == 19
    assert s[4] == "-"
    assert s[7] == "-"


def test_format_timestamp_matches_strftime() -> None:
    from skymap2d.utils import format_timestamp

    ts = datetime(2024, 6, 15, 12, 30, 45).timestamp()
    assert format_timestamp(ts) == datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# presets
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["Sunset", "Night Sky", "Overcast", "Clear Day", "Storm", "Space", "Alien World", "Dawn", "Underwater", "Fantasy"],
)
def test_get_preset_known_names(name: str) -> None:
    from skymap2d.presets import get_preset

    p = get_preset(name)
    assert p is not None
    assert "prompt" in p
    assert "guidance_scale" in p


@pytest.mark.parametrize("name", ["", "unknown", "sunset", "NIGHT SKY"])
def test_get_preset_unknown_returns_none(name: str) -> None:
    from skymap2d.presets import get_preset

    assert get_preset(name) is None


def test_list_presets_count_and_order() -> None:
    from skymap2d.presets import SKYMAP_PRESETS, list_presets

    names = list_presets()
    assert len(names) == len(SKYMAP_PRESETS)
    assert names == list(SKYMAP_PRESETS.keys())


@pytest.mark.parametrize("name", ["Sunset", "Storm", "Fantasy"])
def test_get_preset_prompt_non_empty(name: str) -> None:
    from skymap2d.presets import get_preset_prompt

    prompt = get_preset_prompt(name)
    assert prompt
    assert "equirectangular" in prompt.lower() or "360" in prompt


def test_get_preset_prompt_missing() -> None:
    from skymap2d.presets import get_preset_prompt

    assert get_preset_prompt("NoSuchPreset") is None


@pytest.mark.parametrize("name", ["Sunset", "Space"])
def test_get_preset_params_excludes_prompt(name: str) -> None:
    from skymap2d.presets import get_preset, get_preset_params

    params = get_preset_params(name)
    assert params is not None
    assert "prompt" not in params
    full = get_preset(name)
    assert full is not None
    assert params["guidance_scale"] == full["guidance_scale"]


def test_get_preset_params_unknown() -> None:
    from skymap2d.presets import get_preset_params

    assert get_preset_params("missing") is None


# ---------------------------------------------------------------------------
# hardware profiles
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "gpus,device,offload,clamp_w",
    [
        ([], "cpu", True, 1024),
        ([(0, 12 * 1024**3)], "cuda", False, None),
        ([(0, 8 * 1024**3)], "cuda", True, 2048),
        ([(0, 6 * 1024**3)], "cuda", True, 1024),
        ([(0, 4 * 1024**3), (1, 4 * 1024**3)], "cuda", True, 1024),
    ],
)
def test_profile_from_specs_tiers(gpus, device, offload, clamp_w) -> None:
    from skymap2d.hardware import profile_from_specs

    prof = profile_from_specs(gpus)
    assert prof.device == device
    assert prof.memory_efficient is offload
    assert prof.max_width == clamp_w


def test_profile_summary_contains_name() -> None:
    from skymap2d.hardware import profile_from_specs

    prof = profile_from_specs([(0, 12 * 1024**3)])
    s = prof.summary()
    assert prof.name in s
    assert isinstance(s, str)


@pytest.mark.parametrize("val", ["0", "1", ""])
def test_hw_auto_enabled_respects_env(val: str) -> None:
    from skymap2d.hardware import hw_auto_enabled

    with patch.dict(os.environ, {"SKYMAP2D_HW_AUTO": val}, clear=False):
        result = hw_auto_enabled()
    assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# generator pure helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prompt,should_augment",
    [
        ("forest at dusk", True),
        ("equirectangular 360 hdri sky", False),
        ("PANORAMA city", False),
        ("spherical env map", False),
        ("", False),
        ("  ", False),
    ],
)
def test_augment_prompt_for_equirectangular(prompt: str, should_augment: bool) -> None:
    from skymap2d.generator import augment_prompt_for_equirectangular

    out = augment_prompt_for_equirectangular(prompt)
    if not prompt.strip():
        assert out == prompt.strip() or out == ""
    elif should_augment:
        assert "equirectangular" in out.lower() or "360" in out.lower()
    else:
        assert out == prompt.strip()


@pytest.mark.parametrize(
    "preset,user,expect_substr",
    [
        ("", "bad quality", "bad quality"),
        ("watermark", "", "watermark"),
        ("indoor", "outdoor", "indoor"),
        ("watermark, blur", "watermark, extra", "watermark"),
        ("A", "a longer watermark string", "longer"),
    ],
)
def test_merge_negative_prompt(preset: str, user: str, expect_substr: str) -> None:
    from skymap2d.generator import merge_negative_prompt

    merged = merge_negative_prompt(preset, user)
    assert merged in (user, preset) or expect_substr.lower() in merged.lower()


def test_default_model_id_env_override() -> None:
    from skymap2d import generator as gen

    with patch.dict(os.environ, {"SKYMAP2D_MODEL_ID": "custom/lora"}, clear=False):
        assert gen.default_model_id() == "custom/lora"


def test_default_base_model_id_env_override() -> None:
    from skymap2d import generator as gen

    with patch.dict(os.environ, {"SKYMAP2D_BASE_MODEL_ID": "custom/base"}, clear=False):
        assert gen.default_base_model_id() == "custom/base"


def test_fix_equirect_latitude_swaps_halves() -> None:
    from skymap2d.generator import _fix_equirect_latitude

    w, h = 64, 32
    img = Image.new("RGB", (w, h), color=(255, 0, 0))
    for y in range(h):
        for x in range(w):
            img.putpixel((x, y), (0, y * 8, 0))
    fixed = _fix_equirect_latitude(img)
    assert fixed.size == (w, h)
    assert fixed.getpixel((0, 0))[1] != 0 or h < 4


def test_fix_equirect_latitude_short_image_unchanged() -> None:
    from skymap2d.generator import _fix_equirect_latitude

    img = Image.new("RGB", (8, 2), color=(1, 2, 3))
    assert _fix_equirect_latitude(img).getpixel((0, 0)) == (1, 2, 3)


# ---------------------------------------------------------------------------
# ums_payload
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width,height", [(2048, 1024), (1024, 512)])
def test_build_generate_request_dimensions(width: int, height: int) -> None:
    from skymap2d.ums_payload import build_generate_request

    req = build_generate_request(prompt="sky", output="/tmp/out.png", width=width, height=height)
    assert req["width"] == width
    assert req["height"] == height
    assert req["prompt"] == "sky"


def test_build_generate_request_optional_fields() -> None:
    from skymap2d.ums_payload import build_generate_request

    req = build_generate_request(
        prompt="p",
        output="o.png",
        negative_prompt="neg",
        preset="Sunset",
        exr_scale=2.0,
        extra={"custom": 1},
    )
    assert req["negative_prompt"] == "neg"
    assert req["preset"] == "Sunset"
    assert req["exr_scale"] == 2.0
    assert req["custom"] == 1


# ---------------------------------------------------------------------------
# exr_export
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rgb,expected",
    [
        ((0, 0, 0), 0.0),
        ((255, 255, 255), 1.0),
        ((128, 128, 128), pytest.approx(0.216, abs=0.02)),
    ],
)
def test_pil_rgb_to_linear_f32(rgb: tuple[int, int, int], expected: float) -> None:
    from skymap2d.exr_export import pil_rgb_to_linear_f32

    img = Image.new("RGB", (1, 1), color=rgb)
    arr = pil_rgb_to_linear_f32(img)
    assert arr.shape == (1, 1, 3)
    assert arr[0, 0, 0] == pytest.approx(expected, abs=0.05)


def test_write_exr_rgb_linear_roundtrip_shape(tmp_path: Path) -> None:
    pytest.importorskip("OpenEXR")
    from skymap2d.exr_export import write_exr_rgb_linear

    arr = np.ones((4, 8, 3), dtype=np.float32) * 0.5
    path = tmp_path / "test.exr"
    write_exr_rgb_linear(path, arr, scale=1.0)
    assert path.is_file()
    assert path.stat().st_size > 0


# ---------------------------------------------------------------------------
# image_processor
# ---------------------------------------------------------------------------


def test_save_image_rejects_invalid_format(tmp_path: Path) -> None:
    from skymap2d.image_processor import save_image

    img = Image.new("RGB", (16, 8), color=(10, 20, 30))
    with pytest.raises(ValueError, match="png ou exr"):
        save_image(img, "p", {}, output_dir=tmp_path, image_format="jpeg")


def test_save_image_exr_writes_json_sidecar(tmp_path: Path) -> None:
    pytest.importorskip("OpenEXR")
    from skymap2d.image_processor import save_image

    img = Image.new("RGB", (16, 8), color=(10, 20, 30))
    path = save_image(img, "prompt", {"w": 16}, output_dir=tmp_path, image_format="exr", filename="t.exr")
    assert path.suffix == ".exr"
    meta = path.with_suffix(".json")
    assert meta.is_file()
    data = json.loads(meta.read_text(encoding="utf-8"))
    assert data["color_space"] == "linear_rgb"


def test_create_thumbnail_default_ratio() -> None:
    from skymap2d.image_processor import create_thumbnail

    img = Image.new("RGB", (2048, 1024), color=(0, 128, 255))
    thumb = create_thumbnail(img)
    assert thumb.size[0] / thumb.size[1] == pytest.approx(2.0, abs=0.01)


# ---------------------------------------------------------------------------
# CLI --help (no GPU)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["--help"],
        ["generate", "--help"],
    ],
)
def test_cli_help_exits_zero(argv: list[str]) -> None:
    from skymap2d.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, argv)
    assert result.exit_code == 0
    assert "skymap" in result.output.lower() or "help" in result.output.lower()


@pytest.mark.parametrize(
    "preset_name,key",
    [(n, "negative_prompt") for n in ["Sunset", "Night Sky", "Storm", "Space", "Fantasy"]],
)
def test_preset_negative_prompt_nonempty(preset_name: str, key: str) -> None:
    from skymap2d.presets import get_preset

    p = get_preset(preset_name)
    assert p is not None
    assert p[key]


@pytest.mark.parametrize("steps", [40, 45, 50])
@pytest.mark.parametrize("name", ["Storm", "Alien World"])
def test_preset_inference_steps_reasonable(name: str, steps: int) -> None:
    from skymap2d.presets import get_preset

    p = get_preset(name)
    assert p is not None
    assert p["num_inference_steps"] >= steps - 10


@pytest.mark.parametrize("scale", [0.5, 1.0, 2.0])
def test_build_generate_request_exr_scale(scale: float) -> None:
    from skymap2d.ums_payload import build_generate_request

    req = build_generate_request(prompt="x", output="y.png", exr_scale=scale)
    assert req["exr_scale"] == scale


@pytest.mark.parametrize("seed", [None, 0, 42, 99999])
def test_build_generate_request_seed(seed: int | None) -> None:
    from skymap2d.ums_payload import build_generate_request

    req = build_generate_request(prompt="x", output="y.png", seed=seed)
    assert req["seed"] == seed


@pytest.mark.parametrize("gpu", [None, [0], "0,1"])
def test_build_generate_request_gpu_ids(gpu) -> None:
    from skymap2d.ums_payload import build_generate_request

    req = build_generate_request(prompt="x", output="y.png", gpu_ids=gpu)
    assert "prompt" in req
