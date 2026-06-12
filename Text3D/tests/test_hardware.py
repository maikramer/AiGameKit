"""Testes da auto-detecção de hardware (perfis por VRAM/contagem de GPUs)."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from text3d import hardware
from text3d.cli import cli
from text3d.hardware import GIB, HardwareProfile, detect_hardware_profile, hw_auto_enabled, profile_from_specs


def _gib(n: float) -> int:
    return int(n * GIB)


# ---------------------------------------------------------------------------
# profile_from_specs (puro, sem GPU)
# ---------------------------------------------------------------------------


def test_no_gpu_yields_cpu_fast_profile() -> None:
    p = profile_from_specs([])
    assert p.device == "cpu"
    assert p.gpu_ids is None
    assert p.sdnq_preset is None
    assert p.octree == 128  # preset fast


def test_rtx4050_6gb_gets_int4_fast() -> None:
    """RTX 4050 6GB (~5.6 GiB): balanced estoura VRAM no decode → fast tier."""
    p = profile_from_specs([(0, _gib(6))])
    assert p.device == "cuda"
    assert p.gpu_ids is None
    assert p.sdnq_preset == "sdnq-int4"
    assert (p.steps, p.octree, p.chunks) == (18, 128, 4096)  # fast
    assert p.volume_decoder == "hierarchical"
    assert p.image_width == 1024
    assert p.image_height == 1024


def test_dual_rtx3060_24gb_gets_multigpu_hq_no_quant() -> None:
    """Hardware de referência multi-GPU: 2x RTX 3060 12GB."""
    p = profile_from_specs([(0, _gib(12)), (1, _gib(12))])
    assert p.device == "cuda"
    assert p.gpu_ids == [0, 1]
    assert p.sdnq_preset is None
    assert (p.steps, p.octree, p.chunks) == (30, 384, 20000)  # hq
    assert p.total_vram_gib == 24.0


def test_single_12gb_gets_hq_without_multigpu() -> None:
    p = profile_from_specs([(0, _gib(12))])
    assert p.gpu_ids is None
    assert p.sdnq_preset is None
    assert p.octree == 384


def test_single_8gb_gets_balanced_no_quant() -> None:
    p = profile_from_specs([(0, _gib(8))])
    assert p.sdnq_preset is None
    assert p.octree == 256


def test_tiny_4gb_gets_fast_int4() -> None:
    p = profile_from_specs([(0, _gib(4))])
    assert p.sdnq_preset == "sdnq-int4"
    assert p.octree == 128


def test_multi_gpu_small_vram_sums_capacity() -> None:
    """2x 4GB = 8GB efectivos com split de pesos → balanced sem quant."""
    p = profile_from_specs([(0, _gib(4)), (1, _gib(4))])
    assert p.gpu_ids == [0, 1]
    assert p.sdnq_preset is None
    assert p.octree == 256


# ---------------------------------------------------------------------------
# detect + env kill-switch
# ---------------------------------------------------------------------------


def test_detect_returns_profile() -> None:
    p = detect_hardware_profile()
    assert isinstance(p, HardwareProfile)
    assert p.device in ("cuda", "cpu")


def test_hw_auto_env_kill_switch(monkeypatch) -> None:
    monkeypatch.delenv("TEXT3D_HW_AUTO", raising=False)
    assert hw_auto_enabled() is True
    monkeypatch.setenv("TEXT3D_HW_AUTO", "0")
    assert hw_auto_enabled() is False
    monkeypatch.setenv("TEXT3D_HW_AUTO", "false")
    assert hw_auto_enabled() is False
    monkeypatch.setenv("TEXT3D_HW_AUTO", "1")
    assert hw_auto_enabled() is True


def test_cuda_gpu_specs_without_cuda(monkeypatch) -> None:
    import torch

    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    assert hardware.cuda_gpu_specs() == []


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command", ["generate", "generate-batch"])
def test_cli_exposes_hw_auto_flag(command: str) -> None:
    runner = CliRunner()
    r = runner.invoke(cli, [command, "--help"])
    assert r.exit_code == 0
    # rich-click pode truncar a coluna ("--no-hw-aut…"); basta o prefixo comum.
    assert "--hw-auto" in r.output
