"""Testes do hw-auto do Part3D (puros — sem GPU)."""

from __future__ import annotations

from part3d.hardware import (
    HW_AUTO_ENV,
    LOW_VRAM_MIN_GIB,
    Part3DHardwareProfile,
    detect_hardware_profile,
    hw_auto_enabled,
    profile_from_specs,
)

GIB = 1024**3


def _specs(vram_gib: float, count: int = 1) -> list[tuple[int, int]]:
    return [(i, int(vram_gib * GIB)) for i in range(count)]


class TestHwAutoEnv:
    def test_default_enabled(self, monkeypatch):
        monkeypatch.delenv(HW_AUTO_ENV, raising=False)
        assert hw_auto_enabled() is True

    def test_disabled_with_zero(self, monkeypatch):
        monkeypatch.setenv(HW_AUTO_ENV, "0")
        assert hw_auto_enabled() is False

    def test_disabled_with_false(self, monkeypatch):
        monkeypatch.setenv(HW_AUTO_ENV, "false")
        assert hw_auto_enabled() is False


class TestProfileFromSpecs:
    def test_no_gpu_is_cpu_low_vram(self):
        p = profile_from_specs([])
        assert p.device == "cpu"
        assert p.low_vram is True
        assert p.gpu_ids is None

    def test_small_gpu_activates_low_vram(self):
        p = profile_from_specs(_specs(5.0))
        assert p.device == "cuda"
        assert p.low_vram is True
        assert p.gpu_ids is None
        assert p.total_vram_gib == 5.0

    def test_threshold_boundary_just_above(self):
        p = profile_from_specs(_specs(LOW_VRAM_MIN_GIB + 0.1))
        assert p.low_vram is False

    def test_threshold_boundary_just_below(self):
        p = profile_from_specs(_specs(LOW_VRAM_MIN_GIB - 0.1))
        assert p.low_vram is True

    def test_large_gpu_no_low_vram(self):
        p = profile_from_specs(_specs(12.0))
        assert p.device == "cuda"
        assert p.low_vram is False

    def test_multi_gpu_uses_total_capacity(self):
        p = profile_from_specs(_specs(3.0, count=2))
        assert p.device == "cuda"
        assert p.low_vram is False
        assert p.gpu_ids == [0, 1]
        assert p.total_vram_gib == 6.0

    def test_multi_gpu_each_below_threshold_still_low(self):
        p = profile_from_specs(_specs(2.0, count=2))
        assert p.low_vram is True

    def test_summary_contains_low_vram_when_active(self):
        p = profile_from_specs(_specs(5.0))
        assert "low-vram-mode" in p.summary()

    def test_summary_omits_low_vram_when_inactive(self):
        p = profile_from_specs(_specs(12.0))
        assert "low-vram-mode" not in p.summary()


class TestDetectHardwareProfile:
    def test_returns_profile_instance(self, monkeypatch):
        monkeypatch.setattr("part3d.hardware.cuda_gpu_specs", lambda: [])
        p = detect_hardware_profile()
        assert isinstance(p, Part3DHardwareProfile)
        assert p.device == "cpu"
