"""Testes da auto-detecção de hardware do Text2D (perfis FLUX Klein)."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from text2d.cli import cli
from text2d.generator import HIGH_VRAM_MODEL_ID, LOW_VRAM_MODEL_ID
from text2d.hardware import (
    GIB,
    Text2DHardwareProfile,
    detect_hardware_profile,
    hw_auto_enabled,
    profile_from_specs,
)


def _gib(n: float) -> int:
    return int(n * GIB)


def test_no_gpu_cpu_profile() -> None:
    p = profile_from_specs([])
    assert p.device == "cpu"
    assert p.model_id == LOW_VRAM_MODEL_ID
    assert p.low_vram is True
    assert p.quant_preset == "none"


def test_rtx4050_6gb_4b_int4_offload() -> None:
    """6GB (validado no hardware): 4B int4 não cabe full-GPU → model_cpu offload."""
    p = profile_from_specs([(0, _gib(6))])
    assert p.device == "cuda"
    assert p.model_id == LOW_VRAM_MODEL_ID
    assert p.quant_preset == "sdnq-int4"
    assert p.low_vram is True
    assert p.gpu_ids is None


def test_single_8gb_4b_int4_full_gpu() -> None:
    p = profile_from_specs([(0, _gib(8))])
    assert p.model_id == LOW_VRAM_MODEL_ID
    assert p.quant_preset == "sdnq-int4"
    assert p.low_vram is False


def test_single_12gb_gets_9b_int4_full_gpu() -> None:
    p = profile_from_specs([(0, _gib(12))])
    assert p.model_id == HIGH_VRAM_MODEL_ID
    assert p.quant_preset == "sdnq-int4"
    assert p.low_vram is False
    assert p.gpu_ids is None


def test_dual_rtx3060_gets_9b_multigpu() -> None:
    """Hardware de referência: 2x RTX 3060 12GB → split 9B."""
    p = profile_from_specs([(0, _gib(12)), (1, _gib(12))])
    assert p.model_id == HIGH_VRAM_MODEL_ID
    assert p.low_vram is False
    assert p.gpu_ids == [0, 1]
    assert p.total_vram_gib == 24.0


def test_detect_returns_profile() -> None:
    assert isinstance(detect_hardware_profile(), Text2DHardwareProfile)


def test_env_kill_switch(monkeypatch) -> None:
    monkeypatch.delenv("TEXT2D_HW_AUTO", raising=False)
    assert hw_auto_enabled() is True
    monkeypatch.setenv("TEXT2D_HW_AUTO", "0")
    assert hw_auto_enabled() is False


@pytest.mark.parametrize("command", ["generate", "generate-batch"])
def test_cli_exposes_hw_auto_flag(command: str) -> None:
    runner = CliRunner()
    r = runner.invoke(cli, [command, "--help"])
    assert r.exit_code == 0
    assert "--hw-auto" in r.output
