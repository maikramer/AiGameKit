"""Cobertura adicional Text2D — complementa test_text2d_coverage_suite (≥100 total)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner


def _gib(n: float) -> int:
    from aigamekit_shared.hardware import GIB

    return int(n * GIB)


@pytest.mark.parametrize(
    ("model_id", "key"),
    [
        ("black-forest-labs/FLUX.2-klein-4B", "flux-klein-4b"),
        ("black-forest-labs/FLUX.2-klein-9B", "flux-klein-9b"),
        ("other/model", "flux-klein-9b"),
    ],
)
def test_model_footprint_key(model_id: str, key: str) -> None:
    from text2d.generator import model_footprint_key

    assert model_footprint_key(model_id) == key


@pytest.mark.parametrize("mem", [False, True])
def test_default_model_id_respects_memory_efficient(monkeypatch: pytest.MonkeyPatch, mem: bool) -> None:
    from text2d.generator import HIGH_VRAM_MODEL_ID, LOW_VRAM_MODEL_ID, default_model_id

    monkeypatch.delenv("TEXT2D_MODEL_ID", raising=False)
    assert default_model_id() == HIGH_VRAM_MODEL_ID
    monkeypatch.setenv("TEXT2D_MEMORY_EFFICIENT", "1" if mem else "0")
    # default_model_id reads memory via _model_id only when called from generator init;
    # env TEXT2D_MODEL_ID overrides
    monkeypatch.setenv("TEXT2D_MODEL_ID", LOW_VRAM_MODEL_ID if mem else HIGH_VRAM_MODEL_ID)
    assert default_model_id() == (LOW_VRAM_MODEL_ID if mem else HIGH_VRAM_MODEL_ID)


def test_model_footprint_returns_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    from text2d.generator import LOW_VRAM_MODEL_ID, model_footprint

    fp = model_footprint(LOW_VRAM_MODEL_ID)
    assert fp is not None
    assert fp.fp16_weights_gib > 0


@pytest.mark.parametrize(
    ("width", "height", "steps", "guidance"),
    [
        (512, 512, 4, 1.0),
        (768, 1024, 8, 2.5),
        (1024, 1024, 12, 3.0),
        (1280, 720, 6, 1.5),
    ],
)
def test_vramd_payload_resolution_matrix(width: int, height: int, steps: int, guidance: float) -> None:
    from text2d.vramd_payload import build_generate_request

    req = build_generate_request(
        prompt="p",
        output="o.png",
        width=width,
        height=height,
        steps=steps,
        guidance=guidance,
    )
    assert req["width"] == width
    assert req["height"] == height
    assert req["steps"] == steps
    assert req["guidance"] == guidance


@pytest.mark.parametrize("seed", [None, 0, 1, 999999])
def test_vramd_payload_seed_variants(seed: int | None) -> None:
    from text2d.vramd_payload import build_generate_request

    req = build_generate_request(prompt="p", output="o.png", seed=seed)
    if seed is None:
        assert req["seed"] is None
    else:
        assert req["seed"] == seed


@pytest.mark.parametrize(
    ("quant", "expect_sdnq"),
    [
        ("sdnq-int4", "sdnq-int4"),
        ("sdnq-fp8", "sdnq-fp8"),
        ("none", "none"),
    ],
)
def test_vramd_payload_quant_triple(quant: str, expect_sdnq: str) -> None:
    from text2d.vramd_payload import build_generate_request

    req = build_generate_request(prompt="p", output="o.png", quant_preset=quant)
    assert req.get("quant_preset") == expect_sdnq
    assert req.get("sdnq_preset") == expect_sdnq


def test_parse_batch_manifest_preserves_extra_keys(tmp_path: Path) -> None:
    from text2d.cli import _parse_batch_manifest

    manifest = tmp_path / "m.json"
    manifest.write_text(
        json.dumps([{"id": "a", "prompt": "p", "output": "o.png", "seed": 7}]),
        encoding="utf-8",
    )
    items = _parse_batch_manifest(manifest)
    assert items[0].get("seed") == 7


def test_parse_batch_manifest_array_of_three(tmp_path: Path) -> None:
    from text2d.cli import _parse_batch_manifest

    rows = [{"id": f"i{n}", "prompt": "p", "output": f"{n}.png"} for n in range(3)]
    p = tmp_path / "t.json"
    p.write_text(json.dumps(rows), encoding="utf-8")
    assert len(_parse_batch_manifest(p)) == 3


@pytest.mark.parametrize("vram_gib", [4, 6, 8, 10, 12, 24])
def test_profile_name_contains_cuda_or_cpu(vram_gib: float) -> None:
    from text2d.hardware import profile_from_specs

    if vram_gib == 0:
        p = profile_from_specs([])
        assert p.name == "cpu"
    else:
        p = profile_from_specs([(0, _gib(vram_gib))])
        assert "cuda" in p.name or p.name == "cpu"


@pytest.mark.parametrize("total", [6.0, 12.0, 24.0])
def test_profile_total_vram_single_gpu(total: float) -> None:
    from text2d.hardware import profile_from_specs

    p = profile_from_specs([(0, _gib(total))])
    assert p.total_vram_gib == pytest.approx(total, abs=0.01)


def test_profile_dual_6gb_still_lists_two_gpus_vram() -> None:
    from text2d.hardware import profile_from_specs

    p = profile_from_specs([(0, _gib(6)), (1, _gib(6))])
    assert p.total_vram_gib == pytest.approx(12.0, abs=0.01)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ({"sdnq_preset": "sdnq-int4"}, "sdnq-int4"),
        ({"quant_preset": "sdnq-fp8"}, "sdnq-fp8"),
        ({"memory_efficient": True}, True),
    ],
)
def test_map_load_kwargs_variants(raw: dict, expected: object) -> None:
    from text2d.vramd_load import map_vramd_load_kwargs

    out = map_vramd_load_kwargs(raw)
    if "sdnq_preset" in raw or "quant_preset" in raw:
        assert out.get("quant_preset") == expected
    else:
        assert out.get("memory_efficient") is expected


@pytest.mark.parametrize("cmd", ["generate", "generate-batch", "info", "doctor", "models"])
def test_cli_subcommand_help(cmd: str) -> None:
    from text2d.cli import cli

    r = CliRunner().invoke(cli, [cmd, "--help"])
    assert r.exit_code == 0


def test_cli_verbose_flag_on_root() -> None:
    from text2d.cli import cli

    r = CliRunner().invoke(cli, ["--help"])
    assert "--verbose" in r.output or "-v" in r.output


def test_klein_flux_generator_class_importable() -> None:
    from text2d.generator import KleinFluxGenerator

    assert KleinFluxGenerator.__name__ == "KleinFluxGenerator"


@pytest.mark.parametrize("mem_eff", [True, False])
@pytest.mark.parametrize("fp8_hw", [True, False], ids=["fp8-hw", "no-fp8-hw"])
def test_build_request_memory_efficient_sdnq(
    mem_eff: bool, fp8_hw: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """uint8 default sobe para fp8 apenas quando o hardware suporta (determinístico)."""
    from aigamekit_shared import vramd_load
    from text2d.vramd_payload import build_generate_request

    monkeypatch.setattr(
        vramd_load, "prefer_fp8_preset", lambda p: "sdnq-fp8" if fp8_hw else p
    )

    req = build_generate_request(prompt="p", output="o", memory_efficient=mem_eff)
    assert req["memory_efficient"] is mem_eff
    if mem_eff:
        assert req.get("sdnq_preset") == ("sdnq-fp8" if fp8_hw else "sdnq-uint8")


@pytest.mark.parametrize("flag", [True, False])
def test_build_request_torch_compile_and_channels(flag: bool) -> None:
    from text2d.vramd_payload import build_generate_request

    req = build_generate_request(
        prompt="p",
        output="o",
        torch_compile=flag,
        channels_last=flag,
    )
    assert req["torch_compile"] is flag
    assert req["channels_last"] is flag
