"""Testes da auto-detecção de hardware do Texture2D (pattern-diffusion, SD2-base)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from texture2d.cli import cli
from texture2d.hardware import (
    DEFAULT_HEIGHT,
    DEFAULT_WIDTH,
    GIB,
    Texture2DHardwareProfile,
    detect_hardware_profile,
    hw_auto_enabled,
    profile_from_specs,
)


def _gib(n: float) -> int:
    return int(n * GIB)


def test_no_gpu_cpu_profile() -> None:
    p = profile_from_specs([])
    assert p.device == "cpu"
    assert p.low_vram is True
    assert p.max_width == 512
    assert p.max_height == 512


def test_16gb_full_gpu_no_offload() -> None:
    p = profile_from_specs([(0, _gib(16))])
    assert p.device == "cuda"
    assert p.low_vram is False
    assert p.max_width is None
    assert p.max_height is None


def test_8gb_full_gpu_no_offload() -> None:
    p = profile_from_specs([(0, _gib(8))])
    assert p.device == "cuda"
    assert p.low_vram is False
    assert p.max_width is None
    assert p.max_height is None


def test_7gb_no_offload_clamp_512() -> None:
    p = profile_from_specs([(0, _gib(7))])
    assert p.device == "cuda"
    assert p.low_vram is False
    assert p.max_width == 512
    assert p.max_height == 512


def test_6gb_no_offload_clamp_512() -> None:
    p = profile_from_specs([(0, _gib(6))])
    assert p.device == "cuda"
    assert p.low_vram is False
    assert p.max_width == 512
    assert p.max_height == 512


def test_5gb_lowvram_clamp_512() -> None:
    p = profile_from_specs([(0, _gib(5))])
    assert p.device == "cuda"
    assert p.low_vram is True
    assert p.max_width == 512
    assert p.max_height == 512


def test_4gb_lowvram_clamp_512() -> None:
    p = profile_from_specs([(0, _gib(4))])
    assert p.device == "cuda"
    assert p.low_vram is True
    assert p.max_width == 512
    assert p.max_height == 512


def test_3gb_lowvram_clamp_512() -> None:
    p = profile_from_specs([(0, _gib(3))])
    assert p.device == "cuda"
    assert p.low_vram is True
    assert p.max_width == 512
    assert p.max_height == 512


def test_dual_gpu_sets_gpu_ids() -> None:
    p = profile_from_specs([(0, _gib(12)), (1, _gib(12))])
    assert p.device == "cuda"
    assert p.low_vram is False
    assert p.gpu_ids == [0, 1]
    assert p.total_vram_gib == 24.0


def test_dual_small_gpu_clamp_and_ids() -> None:
    p = profile_from_specs([(0, _gib(6)), (1, _gib(6))])
    assert p.low_vram is False
    assert p.max_width == 512
    assert p.max_height == 512
    assert p.gpu_ids == [0, 1]


def test_detect_returns_profile() -> None:
    assert isinstance(detect_hardware_profile(), Texture2DHardwareProfile)


def test_env_kill_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEXTURE2D_HW_AUTO", raising=False)
    assert hw_auto_enabled() is True
    monkeypatch.setenv("TEXTURE2D_HW_AUTO", "0")
    assert hw_auto_enabled() is False


def test_summary_contains_name() -> None:
    p = profile_from_specs([(0, _gib(16))])
    assert "cuda-1x16g" in p.summary()


@pytest.mark.parametrize("command", ["generate", "batch"])
def test_cli_exposes_hw_auto_flag(command: str) -> None:
    runner = CliRunner()
    r = runner.invoke(cli, [command, "--help"])
    assert r.exit_code == 0
    assert "--hw-auto" in r.output


def test_hw_auto_clamps_higher_resolution() -> None:
    """hw-auto must clamp resolution down to max_width/max_height on small GPUs."""
    p = profile_from_specs([(0, _gib(5))])
    assert p.max_width is not None
    assert p.max_width <= DEFAULT_WIDTH
    assert p.max_height is not None
    assert p.max_height <= DEFAULT_HEIGHT


def test_hw_auto_does_not_clamp_explicit_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    """When user explicitly sets -W, hw-auto must NOT clamp."""
    fake_profile = Texture2DHardwareProfile(
        name="cuda-1x5g",
        device="cuda",
        low_vram=True,
        max_width=512,
        max_height=512,
        gpu_ids=None,
        total_vram_gib=5.0,
    )
    monkeypatch.setattr("texture2d.hardware.detect_hardware_profile", lambda: fake_profile)
    monkeypatch.setattr("gamedev_shared.gpu.warn_if_vram_occupied", lambda: None)

    mock_gen = MagicMock()
    mock_gen.generate.return_value = (MagicMock(), {"seed": 42, "prompt_final": "test"})
    monkeypatch.setattr("texture2d.cli.TextureGenerator", lambda **kw: mock_gen)
    monkeypatch.setattr("texture2d.image_processor.save_image", lambda *a, **kw: Path("/tmp/fake.png"))

    runner = CliRunner()
    r = runner.invoke(cli, ["generate", "test", "-W", "768", "--hw-auto", "--no-pbr", "-o", "/tmp/out.png"])
    assert r.exit_code == 0, r.output
    _, kwargs = mock_gen.generate.call_args
    assert kwargs.get("width") == 768
