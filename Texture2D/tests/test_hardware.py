"""Testes da auto-detecção de hardware do Texture2D (SD1.5 + circular padding).

SD1.5 fp16 (~2.5 GB) cabe em qualquer GPU CUDA moderna — não há offloads, vae
slicing, group offload nem clamp de resolução. O perfil deteta apenas device,
multi-GPU (para display) e VRAM total.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from texture2d.cli import cli
from texture2d.hardware import (
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
    assert p.gpu_ids is None
    assert p.total_vram_gib == 0.0


def test_single_gpu_cuda_no_clamp() -> None:
    """SD1.5 cabe em qualquer GPU — sem clamp de resolução."""
    p = profile_from_specs([(0, _gib(4))])
    assert p.device == "cuda"
    assert p.max_width is None
    assert p.max_height is None


def test_8gb_cuda_no_offload() -> None:
    p = profile_from_specs([(0, _gib(8))])
    assert p.device == "cuda"
    assert p.max_width is None
    assert p.max_height is None


def test_12gb_cuda_no_offload() -> None:
    p = profile_from_specs([(0, _gib(12))])
    assert p.device == "cuda"
    assert p.max_width is None
    assert p.max_height is None


def test_dual_gpu_sets_gpu_ids() -> None:
    p = profile_from_specs([(0, _gib(12)), (1, _gib(12))])
    assert p.device == "cuda"
    assert p.gpu_ids == [0, 1]
    assert p.total_vram_gib == 24.0


def test_dual_small_gpu_no_clamp() -> None:
    """Mesmo GPUs pequenas não precisam de clamp (SD1.5 cabe em 4 GiB)."""
    p = profile_from_specs([(0, _gib(4)), (1, _gib(4))])
    assert p.device == "cuda"
    assert p.max_width is None
    assert p.max_height is None
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
