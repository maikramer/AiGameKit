"""Testes do núcleo genérico de detecção de hardware."""

from __future__ import annotations

from gamedev_shared import hardware


def test_hw_auto_enabled_default_and_kill_switch(monkeypatch) -> None:
    monkeypatch.delenv("X_HW_AUTO", raising=False)
    assert hardware.hw_auto_enabled("X_HW_AUTO") is True
    for off in ("0", "false", "no", "off", "FALSE", " Off "):
        monkeypatch.setenv("X_HW_AUTO", off)
        assert hardware.hw_auto_enabled("X_HW_AUTO") is False
    monkeypatch.setenv("X_HW_AUTO", "1")
    assert hardware.hw_auto_enabled("X_HW_AUTO") is True


def test_cuda_gpu_specs_without_cuda(monkeypatch) -> None:
    import torch

    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    assert hardware.cuda_gpu_specs() == []


def test_cuda_gpu_specs_shapes(monkeypatch) -> None:
    import torch

    class _Props:
        total_memory = 6 * hardware.GIB

    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "device_count", lambda: 2)
    monkeypatch.setattr(torch.cuda, "get_device_properties", lambda i: _Props())
    assert hardware.cuda_gpu_specs() == [(0, 6 * hardware.GIB), (1, 6 * hardware.GIB)]
