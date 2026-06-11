"""Testes da auto-detecção de hardware do Rigging3D (escolha de GPU p/ UniRig)."""

from __future__ import annotations

from click.testing import CliRunner

from rigging3d.cli import cli
from rigging3d.hardware import (
    GIB,
    Rigging3DHardwareProfile,
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
    assert p.low_vram_warning is True


def test_single_6gb_warns_no_pin() -> None:
    """RTX 4050 6GB: sem pin (única GPU), com aviso de VRAM."""
    p = profile_from_specs([(0, _gib(5.5), _gib(6))])
    assert p.gpu_ids is None
    assert p.low_vram_warning is True


def test_single_12gb_no_warn() -> None:
    p = profile_from_specs([(0, _gib(11), _gib(12))])
    assert p.gpu_ids is None
    assert p.low_vram_warning is False


def test_dual_3060_pins_freest_gpu() -> None:
    """2x RTX 3060: GPU 0 ocupada pelo desktop → pina na GPU 1 (mais livre)."""
    p = profile_from_specs([(0, _gib(8), _gib(12)), (1, _gib(11.5), _gib(12))])
    assert p.gpu_ids == [1]
    assert p.free_gib == 11.5
    assert p.low_vram_warning is False


def test_dual_pins_gpu0_when_freest() -> None:
    p = profile_from_specs([(0, _gib(11.9), _gib(12)), (1, _gib(2), _gib(12))])
    assert p.gpu_ids == [0]


def test_detect_returns_profile() -> None:
    assert isinstance(detect_hardware_profile(), Rigging3DHardwareProfile)


def test_env_kill_switch(monkeypatch) -> None:
    monkeypatch.delenv("RIGGING3D_HW_AUTO", raising=False)
    assert hw_auto_enabled() is True
    monkeypatch.setenv("RIGGING3D_HW_AUTO", "0")
    assert hw_auto_enabled() is False


def test_cli_exposes_hw_auto_flag() -> None:
    runner = CliRunner()
    r = runner.invoke(cli, ["--help"])
    assert r.exit_code == 0
    assert "--hw-auto" in r.output


def test_explicit_gpu_ids_wins(monkeypatch) -> None:
    """--gpu-ids explícito não passa pela auto-detecção."""

    def _boom() -> None:  # pragma: no cover
        raise AssertionError("detect não deve correr com --gpu-ids explícito")

    monkeypatch.setattr("rigging3d.hardware.detect_hardware_profile", _boom)
    runner = CliRunner()
    r = runner.invoke(cli, ["--gpu-ids", "1", "--help"])
    assert r.exit_code == 0
