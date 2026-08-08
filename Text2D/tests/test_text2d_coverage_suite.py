"""Suite de cobertura Text2D (vramd_payload, vramd_load, hardware, CLI) sem GPU/HF.

Imports locais onde necessário para não carregar torch na collection.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner


def _gib(n: float) -> int:
    from aigamekit_shared.hardware import GIB

    return int(n * GIB)


# --- build_generate_request ---


def test_vramd_payload_defaults_prompt_output() -> None:
    from text2d.vramd_payload import build_generate_request

    req = build_generate_request(prompt="hello", output="/tmp/a.png")
    assert req["prompt"] == "hello"
    assert req["output"] == "/tmp/a.png"


def test_vramd_payload_default_width_height() -> None:
    from text2d.vramd_payload import build_generate_request

    req = build_generate_request(prompt="p", output="o.png")
    assert req["width"] == 1024
    assert req["height"] == 1024


def test_vramd_payload_default_steps_guidance() -> None:
    from text2d.vramd_payload import build_generate_request

    req = build_generate_request(prompt="p", output="o.png")
    assert req["steps"] == 4
    assert req["guidance"] == 1.0


def test_vramd_payload_seed_none_by_default() -> None:
    from text2d.vramd_payload import build_generate_request

    req = build_generate_request(prompt="p", output="o.png")
    assert req["seed"] is None


def test_vramd_payload_seed_explicit() -> None:
    from text2d.vramd_payload import build_generate_request

    req = build_generate_request(prompt="p", output="o.png", seed=42)
    assert req["seed"] == 42


def test_vramd_payload_model_id_omitted_when_none() -> None:
    from text2d.vramd_payload import build_generate_request

    req = build_generate_request(prompt="p", output="o.png", model_id=None)
    assert "model_id" not in req


def test_vramd_payload_model_id_set() -> None:
    from text2d.vramd_payload import build_generate_request

    mid = "black-forest-labs/FLUX.2-klein-4B"
    req = build_generate_request(prompt="p", output="o.png", model_id=mid)
    assert req["model_id"] == mid


def test_vramd_payload_gpu_ids_list() -> None:
    from text2d.vramd_payload import build_generate_request

    req = build_generate_request(prompt="p", output="o.png", gpu_ids=[0, 1])
    assert req["gpu_ids"] == [0, 1]


def test_vramd_payload_gpu_ids_string_csv() -> None:
    from text2d.vramd_payload import build_generate_request

    req = build_generate_request(prompt="p", output="o.png", gpu_ids="0, 1")
    assert req["gpu_ids"] == [0, 1]


def test_vramd_payload_gpu_ids_empty_string_ignored() -> None:
    from text2d.vramd_payload import build_generate_request

    req = build_generate_request(prompt="p", output="o.png", gpu_ids="  , ")
    assert "gpu_ids" not in req


def test_vramd_payload_quant_preset_maps_sdnq() -> None:
    from text2d.vramd_payload import build_generate_request

    req = build_generate_request(prompt="p", output="o.png", quant_preset="sdnq-int4")
    assert req.get("quant_preset") == "sdnq-int4"
    assert req.get("sdnq_preset") == "sdnq-int4"


def test_vramd_payload_quant_none_string() -> None:
    from text2d.vramd_payload import build_generate_request

    req = build_generate_request(prompt="p", output="o.png", quant_preset="none")
    assert req.get("quant_preset") == "none"
    assert req.get("sdnq_preset") == "none"


def test_vramd_payload_memory_efficient_false_default() -> None:
    from text2d.vramd_payload import build_generate_request

    req = build_generate_request(prompt="p", output="o.png")
    assert req["memory_efficient"] is False


def test_vramd_payload_memory_efficient_true() -> None:
    from text2d.vramd_payload import build_generate_request

    req = build_generate_request(prompt="p", output="o.png", memory_efficient=True)
    assert req["memory_efficient"] is True
    assert req.get("sdnq_preset") == "sdnq-uint8"


def test_vramd_payload_torch_compile_default_off() -> None:
    from text2d.vramd_payload import build_generate_request

    req = build_generate_request(prompt="p", output="o.png")
    assert req["torch_compile"] is False


def test_vramd_payload_torch_compile_on() -> None:
    from text2d.vramd_payload import build_generate_request

    req = build_generate_request(prompt="p", output="o.png", torch_compile=True)
    assert req["torch_compile"] is True


def test_vramd_payload_torch_compile_mode_optional() -> None:
    from text2d.vramd_payload import build_generate_request

    req = build_generate_request(prompt="p", output="o.png", torch_compile_mode="reduce-overhead")
    assert req["torch_compile_mode"] == "reduce-overhead"


def test_vramd_payload_torch_compile_mode_omitted() -> None:
    from text2d.vramd_payload import build_generate_request

    req = build_generate_request(prompt="p", output="o.png")
    assert "torch_compile_mode" not in req


def test_vramd_payload_channels_last_default_false() -> None:
    from text2d.vramd_payload import build_generate_request

    req = build_generate_request(prompt="p", output="o.png")
    assert req["channels_last"] is False


def test_vramd_payload_channels_last_true() -> None:
    from text2d.vramd_payload import build_generate_request

    req = build_generate_request(prompt="p", output="o.png", channels_last=True)
    assert req["channels_last"] is True


def test_vramd_payload_step_cache_set() -> None:
    from text2d.vramd_payload import build_generate_request

    req = build_generate_request(prompt="p", output="o.png", step_cache="fb")
    assert req["step_cache"] == "fb"


def test_vramd_payload_step_cache_omitted() -> None:
    from text2d.vramd_payload import build_generate_request

    req = build_generate_request(prompt="p", output="o.png")
    assert "step_cache" not in req


def test_vramd_payload_extra_merged() -> None:
    from text2d.vramd_payload import build_generate_request

    req = build_generate_request(prompt="p", output="o.png", extra={"quality": "high", "category": "hero"})
    assert req["quality"] == "high"
    assert req["category"] == "hero"


def test_vramd_payload_footprint_key() -> None:
    from text2d.vramd_payload import build_generate_request

    req = build_generate_request(prompt="p", output="o.png", footprint_key="flux-klein-4b")
    assert req["footprint_key"] == "flux-klein-4b"


def test_vramd_payload_coerces_numeric_types() -> None:
    from text2d.vramd_payload import build_generate_request

    req = build_generate_request(prompt=123, output=456, width="512", height="768", steps="8", guidance="2.5")
    assert req["prompt"] == "123"
    assert req["output"] == "456"
    assert req["width"] == 512
    assert req["height"] == 768
    assert req["steps"] == 8
    assert req["guidance"] == 2.5


# --- map_vramd_load_kwargs (extra além de test_vramd_load.py) ---


def test_map_load_sdnq_uint8_to_quant() -> None:
    from text2d.vramd_load import map_vramd_load_kwargs

    out = map_vramd_load_kwargs({"sdnq_preset": "sdnq-uint8"})
    assert out["quant_preset"] == "sdnq-uint8"
    assert "sdnq_preset" not in out


def test_map_load_sdnq_null_becomes_none_quant() -> None:
    from text2d.vramd_load import map_vramd_load_kwargs

    out = map_vramd_load_kwargs({"sdnq_preset": "null"})
    assert out["quant_preset"] is None


def test_map_load_sdnq_empty_string_becomes_none_quant() -> None:
    from text2d.vramd_load import map_vramd_load_kwargs

    out = map_vramd_load_kwargs({"sdnq_preset": ""})
    assert out["quant_preset"] is None


def test_map_load_quant_preset_null_cleared() -> None:
    from text2d.vramd_load import map_vramd_load_kwargs

    out = map_vramd_load_kwargs({"quant_preset": "NULL"})
    assert out["quant_preset"] is None


def test_map_load_quant_preset_wins_over_missing_sdnq() -> None:
    from text2d.vramd_load import map_vramd_load_kwargs

    out = map_vramd_load_kwargs({"quant_preset": "sdnq-fp8"})
    assert out["quant_preset"] == "sdnq-fp8"


def test_map_load_strips_offload_keys() -> None:
    from text2d.vramd_load import map_vramd_load_kwargs

    out = map_vramd_load_kwargs({"offload": "cpu", "allow_group_offload": True, "quant_mode": "int4"})
    assert "offload" not in out
    assert "allow_group_offload" not in out
    assert "quant_mode" not in out


def test_map_load_defaults_verbose_false() -> None:
    from text2d.vramd_load import map_vramd_load_kwargs

    out = map_vramd_load_kwargs({})
    assert out["verbose"] is False


def test_map_load_defaults_torch_compile_mode() -> None:
    from text2d.vramd_load import map_vramd_load_kwargs

    out = map_vramd_load_kwargs({})
    assert out["torch_compile_mode"] == "default"


def test_map_load_memory_efficient_none_becomes_false() -> None:
    from text2d.vramd_load import map_vramd_load_kwargs

    out = map_vramd_load_kwargs({"memory_efficient": None})
    assert out["memory_efficient"] is False


def test_map_load_preserves_unrelated_keys() -> None:
    from text2d.vramd_load import map_vramd_load_kwargs

    out = map_vramd_load_kwargs({"prompt": "x", "width": 1024})
    assert out["prompt"] == "x"
    assert out["width"] == 1024


def test_map_load_does_not_overwrite_explicit_quant_when_sdnq_set() -> None:
    from text2d.vramd_load import map_vramd_load_kwargs

    out = map_vramd_load_kwargs({"quant_preset": "sdnq-int4", "sdnq_preset": "sdnq-uint8"})
    assert out["quant_preset"] == "sdnq-int4"


# --- profile_from_specs / summary / hw_auto ---


def test_profile_cpu_empty_gpus_name() -> None:
    from text2d.hardware import profile_from_specs

    p = profile_from_specs([])
    assert p.name == "cpu"
    assert p.total_vram_gib == 0.0


def test_profile_cpu_summary_tag_4b() -> None:
    from text2d.hardware import profile_from_specs

    p = profile_from_specs([])
    assert "base=4B" in p.summary()


def test_profile_6gb_cuda_name_pattern() -> None:
    from text2d.hardware import profile_from_specs

    p = profile_from_specs([(0, _gib(6))])
    assert p.name.startswith("cuda-1x")
    assert "6" in p.name


def test_profile_12gb_single_9b_summary() -> None:
    from text2d.generator import HIGH_VRAM_MODEL_ID
    from text2d.hardware import profile_from_specs

    p = profile_from_specs([(0, _gib(12))])
    assert p.model_id == HIGH_VRAM_MODEL_ID
    assert "base=9B" in p.summary()


def test_profile_12gb_single_no_cpu_offload_in_summary() -> None:
    from text2d.hardware import profile_from_specs

    p = profile_from_specs([(0, _gib(12))])
    assert "cpu-offload" not in p.summary()


def test_profile_6gb_summary_has_quant_and_offload() -> None:
    from text2d.hardware import profile_from_specs

    p = profile_from_specs([(0, _gib(6))])
    s = p.summary()
    assert "quant=" in s
    assert "cpu-offload" in s


def test_profile_dual_12gb_multigpu_summary_gpus() -> None:
    from text2d.hardware import profile_from_specs

    p = profile_from_specs([(0, _gib(12)), (1, _gib(12))])
    assert "gpus=[0, 1]" in p.summary()


def test_profile_dual_12gb_total_vram_24() -> None:
    from text2d.hardware import profile_from_specs

    p = profile_from_specs([(0, _gib(12)), (1, _gib(12))])
    assert p.total_vram_gib == 24.0


def test_profile_10gb_threshold_9b() -> None:
    from text2d.generator import HIGH_VRAM_MODEL_ID
    from text2d.hardware import profile_from_specs

    p = profile_from_specs([(0, _gib(10))])
    assert p.model_id == HIGH_VRAM_MODEL_ID


def test_profile_9gb_stays_4b() -> None:
    from text2d.generator import LOW_VRAM_MODEL_ID
    from text2d.hardware import profile_from_specs

    p = profile_from_specs([(0, _gib(9))])
    assert p.model_id == LOW_VRAM_MODEL_ID


def test_profile_dual_6gb_not_multigpu_9b_split() -> None:
    from text2d.hardware import profile_from_specs

    p = profile_from_specs([(0, _gib(6)), (1, _gib(6))])
    assert p.gpu_ids is None


def test_hw_auto_default_on(monkeypatch: pytest.MonkeyPatch) -> None:
    from text2d.hardware import hw_auto_enabled

    monkeypatch.delenv("TEXT2D_HW_AUTO", raising=False)
    assert hw_auto_enabled() is True


def test_hw_auto_zero_off(monkeypatch: pytest.MonkeyPatch) -> None:
    from text2d.hardware import hw_auto_enabled

    monkeypatch.setenv("TEXT2D_HW_AUTO", "0")
    assert hw_auto_enabled() is False


@pytest.mark.parametrize(
    "value,expected",
    [
        ("1", True),
        ("true", True),
        ("false", False),
        ("0", False),
    ],
)
def test_hw_auto_env_variants(monkeypatch: pytest.MonkeyPatch, value: str, expected: bool) -> None:
    from text2d.hardware import hw_auto_enabled

    monkeypatch.setenv("TEXT2D_HW_AUTO", value)
    assert hw_auto_enabled() is expected


# --- CLI smoke ---


def test_cli_root_help() -> None:
    from text2d.cli import cli

    r = CliRunner().invoke(cli, ["--help"])
    assert r.exit_code == 0
    assert "generate" in r.output


def test_cli_version() -> None:
    from text2d.cli import cli

    r = CliRunner().invoke(cli, ["--version"])
    assert r.exit_code == 0
    assert "0.1.0" in r.output


def test_cli_generate_help() -> None:
    from text2d.cli import cli

    r = CliRunner().invoke(cli, ["generate", "--help"])
    assert r.exit_code == 0
    assert "--width" in r.output or "-W" in r.output


def test_cli_generate_batch_help() -> None:
    from text2d.cli import cli

    r = CliRunner().invoke(cli, ["generate-batch", "--help"])
    assert r.exit_code == 0


def test_cli_info_help() -> None:
    from text2d.cli import cli

    r = CliRunner().invoke(cli, ["info", "--help"])
    assert r.exit_code == 0


def test_cli_doctor_help() -> None:
    from text2d.cli import cli

    r = CliRunner().invoke(cli, ["doctor", "--help"])
    assert r.exit_code == 0


def test_cli_models_help() -> None:
    from text2d.cli import cli

    r = CliRunner().invoke(cli, ["models", "--help"])
    assert r.exit_code == 0


def test_cli_generate_missing_prompt_fails() -> None:
    from text2d.cli import cli

    r = CliRunner().invoke(cli, ["generate"])
    assert r.exit_code != 0
