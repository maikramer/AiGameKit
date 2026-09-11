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


class TestGroupOffloadProfile:
    """Group offload + streams default ON (padrão tools 2D): offload_mode no perfil."""

    def test_4gb_profile_reports_group_stream(self) -> None:
        """SD1.5 fp16 full (2.4+1.2=3.6 GiB) ficaria a 99% de uma 4 GB → GO."""
        from texture2d.hardware import profile_from_specs

        p = profile_from_specs([(0, _gib(4))])
        assert p.offload_mode == "group_stream"
        assert "group-offload+streams" in p.summary()

    def test_8gb_profile_stays_full_gpu(self) -> None:
        """3.6/7.2 = 50% ≤ 70% → full-GPU com folga (comportamento clássico)."""
        from texture2d.hardware import profile_from_specs

        p = profile_from_specs([(0, _gib(8))])
        assert p.offload_mode == "none"
        assert "group-offload" not in p.summary()

    def test_kill_switch_back_to_classic(self, monkeypatch) -> None:
        monkeypatch.delenv("AIGAMEKIT_GROUP_OFFLOAD", raising=False)
        monkeypatch.setenv("TEXTURE2D_GROUP_OFFLOAD", "0")
        from texture2d.hardware import group_offload_will_engage, profile_from_specs

        p = profile_from_specs([(0, _gib(4))])
        assert p.offload_mode == "none"
        assert group_offload_will_engage() is False

    def test_alloc_conf_no_max_split_when_go(self, monkeypatch) -> None:
        from aigamekit_shared.group_offload import ALLOC_CONF_GROUP_OFFLOAD
        from texture2d.hardware import cuda_alloc_conf_for

        monkeypatch.delenv("AIGAMEKIT_GROUP_OFFLOAD", raising=False)
        monkeypatch.delenv("TEXTURE2D_GROUP_OFFLOAD", raising=False)
        # GO desligado → conf clássico mesmo que a flag peça GO.
        monkeypatch.setenv("TEXTURE2D_GROUP_OFFLOAD", "0")
        assert "max_split_size_mb" in cuda_alloc_conf_for(True)
        assert "max_split_size_mb" not in ALLOC_CONF_GROUP_OFFLOAD

    @pytest.mark.parametrize("command", ["generate", "batch"])
    def test_cli_exposes_group_offload_flag(self, command: str) -> None:
        runner = CliRunner()
        r = runner.invoke(cli, [command, "--help"])
        assert r.exit_code == 0
        assert "--group-offload" in r.output
