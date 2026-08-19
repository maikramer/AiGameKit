"""Cobertura alargada Texture2D (sem GPU): utils, presets, vramd, CLI."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner
from PIL import Image

from texture2d.cli import cli
from texture2d.generator import (
    DEFAULT_GUIDANCE,
    DEFAULT_MODEL_ID,
    DEFAULT_PARAMS,
    DEFAULT_RESOLUTION,
    DEFAULT_STEPS,
    SD_BASE_NEGATIVE,
    default_model_id,
    merge_negative_prompt,
)
from texture2d.hardware import (
    DEFAULT_HEIGHT,
    DEFAULT_WIDTH,
    Texture2DHardwareProfile,
    detect_hardware_profile,
    hw_auto_enabled,
    profile_from_specs,
)
from texture2d.presets import TEXTURE_PRESETS, get_preset, get_preset_params, get_preset_prompt, list_presets
from texture2d.prompt_enhancer import (
    GROUND_DEFAULT_NEGATIVE,
    GROUND_SUFFIX,
    enhance_ground_negative,
    enhance_ground_prompt,
    looks_like_ground,
)
from texture2d.tileability import TileabilityReport, score_tileability
from texture2d.utils import (
    ensure_directory,
    format_bytes,
    format_timestamp,
    generate_seed,
    validate_dimensions,
    validate_params,
    validate_prompt,
)
from texture2d.vramd_payload import build_generate_request

GIB = 1024**3


def _gib(n: float) -> int:
    return int(n * GIB)


def test_generator_default_guidance() -> None:
    assert DEFAULT_GUIDANCE == 7.0


def test_generator_default_steps() -> None:
    assert DEFAULT_STEPS == 30


def test_generator_default_resolution() -> None:
    assert DEFAULT_RESOLUTION == 512


def test_generator_default_model_id_hf() -> None:
    assert "stable-diffusion" in DEFAULT_MODEL_ID


def test_generator_default_params_has_width() -> None:
    assert "width" in DEFAULT_PARAMS


def test_generator_sd_base_negative_nonempty() -> None:
    assert len(SD_BASE_NEGATIVE) > 10


def test_preset_exists_wood() -> None:
    p = get_preset("Wood")
    assert p is not None and "prompt" in p


def test_preset_prompt_wood() -> None:
    assert len(get_preset_prompt("Wood") or "") > 20


def test_preset_params_wood() -> None:
    params = get_preset_params("Wood")
    assert params and 1.0 <= params["guidance_scale"] <= 20.0


def test_preset_exists_fabric() -> None:
    p = get_preset("Fabric")
    assert p is not None and "prompt" in p


def test_preset_prompt_fabric() -> None:
    assert len(get_preset_prompt("Fabric") or "") > 20


def test_preset_params_fabric() -> None:
    params = get_preset_params("Fabric")
    assert params and 1.0 <= params["guidance_scale"] <= 20.0


def test_preset_exists_metal() -> None:
    p = get_preset("Metal")
    assert p is not None and "prompt" in p


def test_preset_prompt_metal() -> None:
    assert len(get_preset_prompt("Metal") or "") > 20


def test_preset_params_metal() -> None:
    params = get_preset_params("Metal")
    assert params and 1.0 <= params["guidance_scale"] <= 20.0


def test_preset_exists_stone() -> None:
    p = get_preset("Stone")
    assert p is not None and "prompt" in p


def test_preset_prompt_stone() -> None:
    assert len(get_preset_prompt("Stone") or "") > 20


def test_preset_params_stone() -> None:
    params = get_preset_params("Stone")
    assert params and 1.0 <= params["guidance_scale"] <= 20.0


def test_preset_exists_brick() -> None:
    p = get_preset("Brick")
    assert p is not None and "prompt" in p


def test_preset_prompt_brick() -> None:
    assert len(get_preset_prompt("Brick") or "") > 20


def test_preset_params_brick() -> None:
    params = get_preset_params("Brick")
    assert params and 1.0 <= params["guidance_scale"] <= 20.0


def test_preset_exists_leather() -> None:
    p = get_preset("Leather")
    assert p is not None and "prompt" in p


def test_preset_prompt_leather() -> None:
    assert len(get_preset_prompt("Leather") or "") > 20


def test_preset_params_leather() -> None:
    params = get_preset_params("Leather")
    assert params and 1.0 <= params["guidance_scale"] <= 20.0


def test_preset_exists_concrete() -> None:
    p = get_preset("Concrete")
    assert p is not None and "prompt" in p


def test_preset_prompt_concrete() -> None:
    assert len(get_preset_prompt("Concrete") or "") > 20


def test_preset_params_concrete() -> None:
    params = get_preset_params("Concrete")
    assert params and 1.0 <= params["guidance_scale"] <= 20.0


def test_preset_exists_marble() -> None:
    p = get_preset("Marble")
    assert p is not None and "prompt" in p


def test_preset_prompt_marble() -> None:
    assert len(get_preset_prompt("Marble") or "") > 20


def test_preset_params_marble() -> None:
    params = get_preset_params("Marble")
    assert params and 1.0 <= params["guidance_scale"] <= 20.0


def test_preset_exists_grass() -> None:
    p = get_preset("Grass")
    assert p is not None and "prompt" in p


def test_preset_prompt_grass() -> None:
    assert len(get_preset_prompt("Grass") or "") > 20


def test_preset_params_grass() -> None:
    params = get_preset_params("Grass")
    assert params and 1.0 <= params["guidance_scale"] <= 20.0


def test_preset_exists_sand() -> None:
    p = get_preset("Sand")
    assert p is not None and "prompt" in p


def test_preset_prompt_sand() -> None:
    assert len(get_preset_prompt("Sand") or "") > 20


def test_preset_params_sand() -> None:
    params = get_preset_params("Sand")
    assert params and 1.0 <= params["guidance_scale"] <= 20.0


def test_preset_exists_dirt() -> None:
    p = get_preset("Dirt")
    assert p is not None and "prompt" in p


def test_preset_prompt_dirt() -> None:
    assert len(get_preset_prompt("Dirt") or "") > 20


def test_preset_params_dirt() -> None:
    params = get_preset_params("Dirt")
    assert params and 1.0 <= params["guidance_scale"] <= 20.0


def test_preset_exists_gravel() -> None:
    p = get_preset("Gravel")
    assert p is not None and "prompt" in p


def test_preset_prompt_gravel() -> None:
    assert len(get_preset_prompt("Gravel") or "") > 20


def test_preset_params_gravel() -> None:
    params = get_preset_params("Gravel")
    assert params and 1.0 <= params["guidance_scale"] <= 20.0


def test_preset_exists_tile_floor() -> None:
    p = get_preset("Tile Floor")
    assert p is not None and "prompt" in p


def test_preset_prompt_tile_floor() -> None:
    assert len(get_preset_prompt("Tile Floor") or "") > 20


def test_preset_params_tile_floor() -> None:
    params = get_preset_params("Tile Floor")
    assert params and 1.0 <= params["guidance_scale"] <= 20.0


def test_list_presets_matches_dict() -> None:
    assert set(list_presets()) == set(TEXTURE_PRESETS.keys())


def test_get_preset_unknown() -> None:
    assert get_preset("__no_such__") is None


def test_validate_prompt_valid_stone() -> None:
    valid, _err = validate_prompt("stone wall")
    assert valid is True


def test_validate_prompt_empty() -> None:
    valid, err = validate_prompt("")
    assert valid is False
    assert err is not None


def test_validate_prompt_spaces() -> None:
    valid, err = validate_prompt("   ")
    assert valid is False
    assert err is not None


def test_validate_prompt_max_ok() -> None:
    valid, _err = validate_prompt(
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    )
    assert valid is True


def test_validate_prompt_max_over() -> None:
    valid, err = validate_prompt(
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    )
    assert valid is False
    assert err is not None


def test_validate_dimensions_256x256() -> None:
    assert validate_dimensions(256, 256)[0] is True


def test_validate_dimensions_512x512() -> None:
    assert validate_dimensions(512, 512)[0] is True


def test_validate_dimensions_2048x2048() -> None:
    assert validate_dimensions(2048, 2048)[0] is True


def test_validate_dimensions_255x512() -> None:
    assert validate_dimensions(255, 512)[0] is False


def test_validate_dimensions_2049x512() -> None:
    assert validate_dimensions(2049, 512)[0] is False


def test_validate_dimensions_512x513() -> None:
    assert validate_dimensions(512, 513)[0] is False


def test_validate_params_defaults_ok() -> None:
    assert validate_params({"guidance_scale": 7.5, "num_inference_steps": 50, "width": 512, "height": 512})[0] is True


def test_validate_params_guidance_low() -> None:
    assert validate_params({"guidance_scale": 0.5})[0] is False


def test_validate_params_guidance_high() -> None:
    assert validate_params({"guidance_scale": 21.0})[0] is False


def test_validate_params_steps_low() -> None:
    assert validate_params({"num_inference_steps": 5})[0] is False


def test_validate_params_steps_high() -> None:
    assert validate_params({"num_inference_steps": 101})[0] is False


def test_vramd_payload_core() -> None:
    req = build_generate_request(prompt="grass", output="/tmp/out.png")
    assert req["prompt"] == "grass"
    assert req["width"] == 512


def test_vramd_payload_dims() -> None:
    req = build_generate_request(prompt="x", output="o.png", width=1024, height=768, steps=40, guidance=8.5)
    assert req["width"] == 1024
    assert req["steps"] == 40


def test_vramd_payload_neg_preset() -> None:
    req = build_generate_request(prompt="p", output="o.png", negative_prompt="blur", preset="Stone")
    assert req["negative_prompt"] == "blur"


def test_vramd_payload_model_ground() -> None:
    """Ground é tri-state ("auto"/"on"/"off") — bool nunca devia chegar ao gerador."""
    req = build_generate_request(prompt="p", output="o.png", model_id="m/id", ground=True)
    assert req["ground"] == "on"
    req_off = build_generate_request(prompt="p", output="o.png", ground=False)
    assert req_off["ground"] == "off"
    req_auto = build_generate_request(prompt="p", output="o.png", ground="auto")
    assert req_auto["ground"] == "auto"
    # Sem ground: chave omitida → o adapter aplica "auto".


def test_vramd_payload_extra() -> None:
    req = build_generate_request(prompt="p", output="o.png", extra={"quality": "high"})
    assert req["quality"] == "high"


def test_vramd_payload_seed_0() -> None:
    req = build_generate_request(prompt="p", output="o.png", seed=2000)
    assert req["seed"] == 2000


def test_vramd_payload_seed_1() -> None:
    req = build_generate_request(prompt="p", output="o.png", seed=2001)
    assert req["seed"] == 2001


def test_vramd_payload_seed_2() -> None:
    req = build_generate_request(prompt="p", output="o.png", seed=2002)
    assert req["seed"] == 2002


def test_vramd_payload_seed_3() -> None:
    req = build_generate_request(prompt="p", output="o.png", seed=2003)
    assert req["seed"] == 2003


def test_vramd_payload_seed_4() -> None:
    req = build_generate_request(prompt="p", output="o.png", seed=2004)
    assert req["seed"] == 2004


def test_vramd_payload_seed_5() -> None:
    req = build_generate_request(prompt="p", output="o.png", seed=2005)
    assert req["seed"] == 2005


def test_vramd_payload_seed_6() -> None:
    req = build_generate_request(prompt="p", output="o.png", seed=2006)
    assert req["seed"] == 2006


def test_vramd_payload_seed_7() -> None:
    req = build_generate_request(prompt="p", output="o.png", seed=2007)
    assert req["seed"] == 2007


def test_vramd_payload_seed_8() -> None:
    req = build_generate_request(prompt="p", output="o.png", seed=2008)
    assert req["seed"] == 2008


def test_vramd_payload_seed_9() -> None:
    req = build_generate_request(prompt="p", output="o.png", seed=2009)
    assert req["seed"] == 2009


def test_hardware_profile_cpu() -> None:
    p = profile_from_specs([])
    assert p.max_width is None and p.max_height is None


def test_hardware_profile_single_4g() -> None:
    p = profile_from_specs([(0, _gib(4))])
    assert p.max_width is None and p.max_height is None


def test_hardware_profile_single_16g() -> None:
    p = profile_from_specs([(0, _gib(16))])
    assert p.max_width is None and p.max_height is None


def test_hardware_profile_dual_12g() -> None:
    p = profile_from_specs([(0, _gib(12)), (1, _gib(12))])
    assert p.max_width is None and p.max_height is None


def test_hardware_constants() -> None:
    assert DEFAULT_WIDTH == 512 and DEFAULT_HEIGHT == 512


def test_detect_hardware_profile_type() -> None:
    assert isinstance(detect_hardware_profile(), Texture2DHardwareProfile)


def test_looks_like_ground_grass() -> None:
    assert looks_like_ground("grass") is True


def test_looks_like_ground_metal_plate() -> None:
    assert looks_like_ground("metal plate") is False


def test_looks_like_ground_forest_floor() -> None:
    assert looks_like_ground("forest floor") is True


def test_looks_like_ground_abstract_gradie() -> None:
    assert looks_like_ground("abstract gradient") is False


def test_enhance_ground_prompt_adds_suffix() -> None:
    out = enhance_ground_prompt("grass field")
    assert "top-down" in out.lower() or "seamless" in out.lower()


def test_enhance_ground_prompt_empty() -> None:
    assert enhance_ground_prompt("") == ""


def test_enhance_ground_prompt_already_covered() -> None:
    p = "grass, top-down close-up, flat diffuse lighting, seamless tileable, medium-scale"
    assert GROUND_SUFFIX not in enhance_ground_prompt(p)


def test_enhance_ground_negative_merges() -> None:
    assert len(enhance_ground_negative("")) > 20


def test_enhance_ground_negative_no_dup() -> None:
    assert enhance_ground_negative("aerial view, blur").lower().count("aerial view") == 1


def test_ground_constants() -> None:
    assert "tileable" in GROUND_SUFFIX and "isometric" in GROUND_DEFAULT_NEGATIVE


def test_merge_negative_preset_only() -> None:
    assert merge_negative_prompt("blur", "") == "blur"


def test_merge_negative_user_only() -> None:
    assert merge_negative_prompt("", "noise") == "noise"


def test_merge_negative_both() -> None:
    assert merge_negative_prompt("a", "b") == "a, b"


def test_merge_negative_subset() -> None:
    assert merge_negative_prompt("blur", "no blur please") == "no blur please"


def test_generate_seed_range() -> None:
    for _ in range(15):
        assert 0 <= generate_seed() < 2**32


def test_format_bytes_kb_mb() -> None:
    assert "KB" in format_bytes(2048) and "MB" in format_bytes(5 * 1024 * 1024)


def test_format_timestamp_len() -> None:
    assert len(format_timestamp(1_700_000_000.0)) == 19


def test_ensure_directory_nested(tmp_path: Path) -> None:
    d = tmp_path / "nested" / "dir"
    ensure_directory(d)
    assert d.is_dir()


def test_default_model_id_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEXTURE2D_MODEL_ID", "org/custom")
    assert default_model_id() == "org/custom"


def test_tileability_uniform_image() -> None:
    img = Image.new("RGB", (128, 128), color=(10, 20, 30))
    report = score_tileability(img)
    assert report.score >= 0.99


def test_tileability_summary_pass() -> None:
    assert "PASS" in TileabilityReport(0.9, 1.0, 1.0, 0, 64, 64).summary()


def test_tileability_summary_fail() -> None:
    assert "FAIL" in TileabilityReport(0.5, 50.0, 50.0, 100, 64, 64).summary()


def test_tileability_to_dict_verdict() -> None:
    assert TileabilityReport(0.88, 1.0, 1.0, 0, 64, 64).to_dict()["verdict"] == "PASS"


def test_tileability_tiny_raises() -> None:
    with pytest.raises(ValueError):
        score_tileability(Image.new("RGB", (1, 64)))


def test_cli_root_help() -> None:
    runner = CliRunner()
    r = runner.invoke(cli, ["--help"])
    assert r.exit_code == 0
    assert "generate" in r.output or "generate" in r.output.lower()


def test_cli_generate_help() -> None:
    runner = CliRunner()
    r = runner.invoke(cli, ["generate", "--help"])
    assert r.exit_code == 0
    assert "--ground" in r.output or "--ground" in r.output.lower()


def test_cli_batch_help() -> None:
    runner = CliRunner()
    r = runner.invoke(cli, ["batch", "--help"])
    assert r.exit_code == 0
    assert "--preset" in r.output or "--preset" in r.output.lower()


def test_cli_presets_cmd() -> None:
    runner = CliRunner()
    r = runner.invoke(cli, ["presets", "--help"])
    assert r.exit_code == 0
    assert "List" in r.output or "List" in r.output.lower()


def test_cli_validate_tileable_help() -> None:
    runner = CliRunner()
    r = runner.invoke(cli, ["validate-tileable", "--help"])
    assert r.exit_code == 0
    assert "tile" in r.output.lower()


def test_hw_auto_1() -> None:
    import os

    old = os.environ.get("TEXTURE2D_HW_AUTO")
    os.environ["TEXTURE2D_HW_AUTO"] = "1"
    try:
        assert hw_auto_enabled() is True
    finally:
        if old is None:
            os.environ.pop("TEXTURE2D_HW_AUTO", None)
        else:
            os.environ["TEXTURE2D_HW_AUTO"] = old


def test_hw_auto_0() -> None:
    import os

    old = os.environ.get("TEXTURE2D_HW_AUTO")
    os.environ["TEXTURE2D_HW_AUTO"] = "0"
    try:
        assert hw_auto_enabled() is False
    finally:
        if old is None:
            os.environ.pop("TEXTURE2D_HW_AUTO", None)
        else:
            os.environ["TEXTURE2D_HW_AUTO"] = old


def test_hw_auto_false() -> None:
    import os

    old = os.environ.get("TEXTURE2D_HW_AUTO")
    os.environ["TEXTURE2D_HW_AUTO"] = "false"
    try:
        assert hw_auto_enabled() is False
    finally:
        if old is None:
            os.environ.pop("TEXTURE2D_HW_AUTO", None)
        else:
            os.environ["TEXTURE2D_HW_AUTO"] = old


def test_generate_help_flag_hwauto() -> None:
    runner = CliRunner()
    r = runner.invoke(cli, ["generate", "--help"])
    assert r.exit_code == 0
    assert "--hw-auto" in r.output


def test_generate_help_flag_hw_auto_pair() -> None:
    runner = CliRunner()
    r = runner.invoke(cli, ["generate", "--help"])
    assert r.exit_code == 0
    assert "--hw-auto" in r.output


def test_generate_help_flag_umspriority() -> None:
    runner = CliRunner()
    r = runner.invoke(cli, ["generate", "--help"])
    assert r.exit_code == 0
    assert "--vramd-priority" in r.output


def test_generate_help_flag_noums() -> None:
    runner = CliRunner()
    r = runner.invoke(cli, ["generate", "--help"])
    assert r.exit_code == 0
    assert "--no-vramd" in r.output


def test_generate_help_flag_quality() -> None:
    runner = CliRunner()
    r = runner.invoke(cli, ["generate", "--help"])
    assert r.exit_code == 0
    assert "--quality" in r.output


def test_generate_help_flag_cpu() -> None:
    runner = CliRunner()
    r = runner.invoke(cli, ["generate", "--help"])
    assert r.exit_code == 0
    assert "--cpu" in r.output


def test_ums_dims_case_0() -> None:
    req = build_generate_request(prompt="p", output="o.png", width=512, height=512)
    assert req["width"] == 512 and req["height"] == 512


def test_ums_dims_case_1() -> None:
    req = build_generate_request(prompt="p", output="o.png", width=768, height=768)
    assert req["width"] == 768 and req["height"] == 768


def test_ums_dims_case_2() -> None:
    req = build_generate_request(prompt="p", output="o.png", width=1024, height=512)
    assert req["width"] == 1024 and req["height"] == 512


def test_ums_dims_case_3() -> None:
    req = build_generate_request(prompt="p", output="o.png", width=640, height=480)
    assert req["width"] == 640 and req["height"] == 480


def test_ums_dims_case_4() -> None:
    req = build_generate_request(prompt="p", output="o.png", width=896, height=896)
    assert req["width"] == 896 and req["height"] == 896
