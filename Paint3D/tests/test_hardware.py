"""Testes da auto-detecção de hardware do Paint3D (Hunyuan3D-Paint 2.1)."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from paint3d.cli import cli
from paint3d.hardware import (
    GIB,
    Paint3DHardwareProfile,
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
    assert p.max_views == 4
    assert p.view_resolution == 384
    assert p.render_size == 1024
    assert p.texture_size == 2048


def test_rtx4050_6gb_gets_low_vram() -> None:
    """RTX 4050 6GB → SDNQ uint8, 4v@384, render 1024, tex 2048."""
    p = profile_from_specs([(0, _gib(6))])
    assert p.device == "cuda"
    assert p.low_vram is True
    assert p.max_views == 4
    assert p.view_resolution == 384
    assert p.render_size == 1024
    assert p.texture_size == 2048
    assert p.gpu_ids is None


def test_rtx4060_8gb_gets_mid_tier() -> None:
    """RTX 4060 8GB → FP16, 6v@512, render 1536, tex 3072."""
    p = profile_from_specs([(0, _gib(8))])
    assert p.low_vram is False
    assert p.max_views == 6
    assert p.view_resolution == 512
    assert p.render_size == 1536
    assert p.texture_size == 3072


def test_single_12gb_gets_fp16_default() -> None:
    """12GB → FP16, no overrides (uses CLI defaults)."""
    p = profile_from_specs([(0, _gib(12))])
    assert p.low_vram is False
    assert p.max_views is None
    assert p.render_size is None
    assert p.texture_size is None
    assert p.gpu_ids is None


def test_dual_rtx3060_gets_fp16_multigpu() -> None:
    """2x RTX 3060 12GB → FP16 multi-GPU."""
    p = profile_from_specs([(0, _gib(12)), (1, _gib(12))])
    assert p.low_vram is False
    assert p.gpu_ids == [0, 1]
    assert p.max_views is None
    assert p.total_vram_gib == 24.0


def test_single_7gb_mid_tier() -> None:
    """7GB → still low_vram (below 8.0 threshold)."""
    p = profile_from_specs([(0, _gib(7))])
    assert p.low_vram is True
    assert p.render_size == 1024
    assert p.texture_size == 2048


def test_single_9gb_mid_tier() -> None:
    """9GB → mid tier (8.0-10.0)."""
    p = profile_from_specs([(0, _gib(9))])
    assert p.low_vram is False
    assert p.render_size == 1536
    assert p.texture_size == 3072


def test_detect_returns_profile() -> None:
    assert isinstance(detect_hardware_profile(), Paint3DHardwareProfile)


def test_env_kill_switch(monkeypatch) -> None:
    monkeypatch.delenv("PAINT3D_HW_AUTO", raising=False)
    assert hw_auto_enabled() is True
    monkeypatch.setenv("PAINT3D_HW_AUTO", "0")
    assert hw_auto_enabled() is False


@pytest.mark.parametrize("command", ["texture", "texture-batch"])
def test_cli_exposes_hw_auto_flag(command: str) -> None:
    runner = CliRunner()
    r = runner.invoke(cli, [command, "--help"])
    assert r.exit_code == 0
    assert "--hw-auto" in r.output
