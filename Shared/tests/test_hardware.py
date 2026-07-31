"""Testes do núcleo genérico de detecção de hardware."""

from __future__ import annotations

import sys

import pytest

from aigamekit_shared import hardware


def test_hw_auto_enabled_default_and_kill_switch(monkeypatch) -> None:
    monkeypatch.delenv("X_HW_AUTO", raising=False)
    assert hardware.hw_auto_enabled("X_HW_AUTO") is True
    for off in ("0", "false", "no", "off", "FALSE", " Off "):
        monkeypatch.setenv("X_HW_AUTO", off)
        assert hardware.hw_auto_enabled("X_HW_AUTO") is False
    monkeypatch.setenv("X_HW_AUTO", "1")
    assert hardware.hw_auto_enabled("X_HW_AUTO") is True


def test_cuda_gpu_specs_without_torch(monkeypatch) -> None:
    """Sem torch instalado, as listas de specs são vazias (não ImportError)."""
    monkeypatch.setitem(sys.modules, "torch", None)
    assert hardware.cuda_gpu_specs() == []
    assert hardware.cuda_gpu_free_specs() == []


def test_cuda_gpu_specs_without_cuda(monkeypatch) -> None:
    torch = pytest.importorskip("torch")

    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    assert hardware.cuda_gpu_specs() == []


def test_cuda_gpu_specs_shapes(monkeypatch) -> None:
    torch = pytest.importorskip("torch")

    class _Props:
        total_memory = 6 * hardware.GIB

    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "device_count", lambda: 2)
    monkeypatch.setattr(torch.cuda, "get_device_properties", lambda i: _Props())
    assert hardware.cuda_gpu_specs() == [(0, 6 * hardware.GIB), (1, 6 * hardware.GIB)]


# ---------------------------------------------------------------------------
# HardwareProfileBase + detect_profile (base das 8 tools com hardware.py)
# ---------------------------------------------------------------------------


def test_hardware_profile_base_fields() -> None:
    from dataclasses import fields

    from aigamekit_shared.hardware import HardwareProfileBase

    names = [f.name for f in fields(HardwareProfileBase)]
    assert names == ["name", "device", "gpu_ids", "total_vram_gib"]


def test_hardware_profile_base_frozen_and_eq() -> None:
    from dataclasses import FrozenInstanceError

    from aigamekit_shared.hardware import HardwareProfileBase

    p = HardwareProfileBase(name="cuda-1x6g", device="cuda", gpu_ids=None, total_vram_gib=6.0)
    with pytest.raises(FrozenInstanceError):
        p.name = "x"  # type: ignore[misc]
    q = HardwareProfileBase(name="cuda-1x6g", device="cuda", gpu_ids=None, total_vram_gib=6.0)
    assert p == q


def test_hardware_profile_base_subclass_extends() -> None:
    """Padrão das tools: herdar a base e acrescentar campos do planner."""
    from dataclasses import dataclass, fields

    from aigamekit_shared.hardware import HardwareProfileBase

    @dataclass(frozen=True)
    class _FakeProfile(HardwareProfileBase):
        memory_efficient: bool
        sdnq_preset: str | None = None

    p = _FakeProfile(name="cuda", device="cuda", gpu_ids=[0], total_vram_gib=8.0, memory_efficient=True)
    assert p.gpu_ids == [0] and p.memory_efficient is True and p.sdnq_preset is None
    names = [f.name for f in fields(_FakeProfile)]
    assert names == ["name", "device", "gpu_ids", "total_vram_gib", "memory_efficient", "sdnq_preset"]


def test_detect_profile_delegates_to_profile_from_specs(monkeypatch) -> None:
    from aigamekit_shared.hardware import detect_profile

    captured: dict[str, object] = {}

    def _fake_from_specs(gpus: list[tuple[int, int]]) -> str:
        captured["gpus"] = gpus
        return "profile-ok"

    monkeypatch.setattr("aigamekit_shared.hardware.cuda_gpu_specs", lambda: [(0, 6 * hardware.GIB)])
    assert detect_profile(_fake_from_specs) == "profile-ok"
    assert captured["gpus"] == [(0, 6 * hardware.GIB)]


def test_detect_profile_empty_specs(monkeypatch) -> None:
    from aigamekit_shared.hardware import detect_profile

    monkeypatch.setattr("aigamekit_shared.hardware.cuda_gpu_specs", lambda: [])
    assert detect_profile(lambda gpus: len(gpus)) == 0
