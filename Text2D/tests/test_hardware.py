"""Testes da auto-detecção de hardware do Text2D (perfis FLUX Klein)."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from text2d.cli import cli
from text2d.generator import HIGH_VRAM_MODEL_ID, LOW_VRAM_MODEL_ID
from text2d.hardware import (
    ALLOC_CONF_DEFAULT,
    ALLOC_CONF_GROUP_OFFLOAD,
    GIB,
    Text2DHardwareProfile,
    detect_hardware_profile,
    group_offload_will_engage,
    hw_auto_enabled,
    profile_from_specs,
)


def _gib(n: float) -> int:
    return int(n * GIB)


def test_no_gpu_cpu_profile() -> None:
    p = profile_from_specs([])
    assert p.device == "cpu"
    assert p.model_id == LOW_VRAM_MODEL_ID
    assert p.memory_efficient is True
    assert p.quant_preset == "none"


def test_rtx4050_6gb_4b_int4_group_stream() -> None:
    """6GB (validado no hardware): 4B int4 + group offload com streams."""
    p = profile_from_specs([(0, _gib(6))])
    assert p.device == "cuda"
    assert p.model_id == LOW_VRAM_MODEL_ID
    assert p.quant_preset == "sdnq-int4"
    assert p.offload_mode == "group_stream"
    assert p.memory_efficient is True
    assert p.gpu_ids is None


def test_single_8gb_4b_int4_group_stream_no_full_gpu() -> None:
    """8GB: int4 full-GPU ficaria a 83% do orçamento (sem folga) → GO+streams."""
    p = profile_from_specs([(0, _gib(8))])
    assert p.model_id == LOW_VRAM_MODEL_ID
    assert p.quant_preset == "sdnq-int4"
    assert p.offload_mode == "group_stream"
    assert p.memory_efficient is True


def test_single_12gb_gets_9b_int4_group_stream() -> None:
    """12GB: 9B int4 full ficaria a 91% do orçamento → GO+streams (folga real)."""
    p = profile_from_specs([(0, _gib(12))])
    assert p.model_id == HIGH_VRAM_MODEL_ID
    assert p.quant_preset == "sdnq-int4"
    assert p.offload_mode == "group_stream"
    assert p.memory_efficient is True
    assert p.gpu_ids is None


def test_single_16gb_9b_int4_full_gpu_with_headroom() -> None:
    """16GB: 9B int4 full-GPU fica a ~68% do orçamento — folga suficiente."""
    p = profile_from_specs([(0, _gib(16))])
    assert p.model_id == HIGH_VRAM_MODEL_ID
    assert p.quant_preset == "sdnq-int4"
    assert p.offload_mode == "none"
    assert p.memory_efficient is False


def test_dual_rtx3060_gets_9b_multigpu() -> None:
    """Hardware de referência: 2x RTX 3060 12GB → split 9B."""
    p = profile_from_specs([(0, _gib(12)), (1, _gib(12))])
    assert p.model_id == HIGH_VRAM_MODEL_ID
    assert p.memory_efficient is False
    assert p.gpu_ids == [0, 1]
    assert p.total_vram_gib == 24.0


def test_4gb_descends_to_int3_group_stream() -> None:
    """4GB: headroom GO apertado (<2.5 GiB) com int4 → desce um bit para int3."""
    p = profile_from_specs([(0, _gib(4))])
    assert p.model_id == LOW_VRAM_MODEL_ID
    assert p.quant_preset == "sdnq-int3"
    assert p.offload_mode == "group_stream"


def test_3gb_descends_to_int2_group_stream() -> None:
    """3GB: headroom GO <1.5 GiB → último recurso int2."""
    p = profile_from_specs([(0, _gib(3))])
    assert p.quant_preset == "sdnq-int2"
    assert p.offload_mode == "group_stream"


def test_group_offload_kill_switch_back_to_classic(monkeypatch) -> None:
    """TEXT2D_GROUP_OFFLOAD=0 desliga o GO: 8GB volta a full-GPU int4 clássico."""
    monkeypatch.delenv("AIGAMEKIT_GROUP_OFFLOAD", raising=False)
    monkeypatch.setenv("TEXT2D_GROUP_OFFLOAD", "0")
    p = profile_from_specs([(0, _gib(8))])
    assert p.offload_mode == "none"
    assert p.memory_efficient is False
    assert group_offload_will_engage() is False


def test_alloc_conf_by_mode(monkeypatch) -> None:
    """Sem max_split_size_mb quando GO vai correr (fragmentação sob churn)."""
    from text2d.hardware import cuda_alloc_conf_for

    monkeypatch.delenv("AIGAMEKIT_GROUP_OFFLOAD", raising=False)
    monkeypatch.delenv("TEXT2D_GROUP_OFFLOAD", raising=False)
    # GO desligado → conf clássico mesmo em GPU pequena.
    monkeypatch.setenv("TEXT2D_GROUP_OFFLOAD", "0")
    assert cuda_alloc_conf_for(True) == ALLOC_CONF_DEFAULT
    assert "max_split_size_mb" in ALLOC_CONF_DEFAULT
    assert "max_split_size_mb" not in ALLOC_CONF_GROUP_OFFLOAD


def test_group_offload_will_engage_pure(monkeypatch) -> None:
    """Gate puro: só pergunta o perfil (specs) — sem torch/CUDA no path."""
    monkeypatch.delenv("AIGAMEKIT_GROUP_OFFLOAD", raising=False)
    monkeypatch.delenv("TEXT2D_GROUP_OFFLOAD", raising=False)

    import text2d.hardware as hw

    orig = hw.detect_hardware_profile

    class _P:
        device = "cuda"
        offload_mode = "group_stream"

    try:
        hw.detect_hardware_profile = lambda: _P()  # type: ignore[assignment]
        assert group_offload_will_engage() is True

        class _Q:
            device = "cuda"
            offload_mode = "none"

        hw.detect_hardware_profile = lambda: _Q()  # type: ignore[assignment]
        assert group_offload_will_engage() is False
    finally:
        hw.detect_hardware_profile = orig  # type: ignore[assignment]


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


@pytest.mark.parametrize("command", ["generate", "generate-batch"])
def test_cli_exposes_group_offload_flag(command: str) -> None:
    """--group-offload/--no-group-offload existe e é default ON."""
    runner = CliRunner()
    r = runner.invoke(cli, [command, "--help"])
    assert r.exit_code == 0
    assert "--group-offload" in r.output
