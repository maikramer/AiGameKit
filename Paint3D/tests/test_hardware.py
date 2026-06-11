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


def test_rtx4050_6gb_gets_low_vram() -> None:
    """Hardware de referência mono-GPU: RTX 4050 6GB → SDNQ uint8, 4v@384."""
    p = profile_from_specs([(0, _gib(6))])
    assert p.device == "cuda"
    assert p.low_vram is True
    assert p.gpu_ids is None


def test_single_12gb_gets_fp16_default() -> None:
    p = profile_from_specs([(0, _gib(12))])
    assert p.low_vram is False
    assert p.gpu_ids is None


def test_dual_rtx3060_gets_fp16_multigpu() -> None:
    """Hardware de referência multi-GPU: 2x RTX 3060 12GB."""
    p = profile_from_specs([(0, _gib(12)), (1, _gib(12))])
    assert p.low_vram is False
    assert p.gpu_ids == [0, 1]
    assert p.total_vram_gib == 24.0


def test_single_8gb_still_low_vram() -> None:
    """Defaults FP16 são afinados p/ 12GB; 8GB fica em low-VRAM."""
    p = profile_from_specs([(0, _gib(8))])
    assert p.low_vram is True


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
