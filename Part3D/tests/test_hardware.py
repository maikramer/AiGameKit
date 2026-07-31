"""Testes do hw-auto do Part3D (puros — sem GPU)."""

from __future__ import annotations

from part3d.hardware import (
    FULL_PROFILE_MIN_GIB,
    HW_AUTO_ENV,
    MID_TIER_MIN_GIB,
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
        assert p.memory_efficient is True
        assert p.cpu_offload is True
        assert p.sdnq_preset == "sdnq-uint8"
        assert p.gpu_ids is None

    def test_small_gpu_activates_memory_efficient(self):
        p = profile_from_specs(_specs(5.0))
        assert p.device == "cuda"
        assert p.memory_efficient is True
        assert p.cpu_offload is True
        assert p.sdnq_preset == "sdnq-uint8"
        assert p.gpu_ids is None
        assert p.total_vram_gib == 5.0

    def test_mid_tier_offload_no_sdnq(self):
        p = profile_from_specs(_specs(MID_TIER_MIN_GIB + 0.5))
        assert p.memory_efficient is False
        assert p.cpu_offload is True
        assert p.sdnq_preset is None

    def test_full_profile_no_offload(self):
        p = profile_from_specs(_specs(FULL_PROFILE_MIN_GIB + 0.1))
        assert p.memory_efficient is False
        assert p.cpu_offload is False
        assert p.sdnq_preset is None

    def test_boundary_just_below_mid(self):
        p = profile_from_specs(_specs(MID_TIER_MIN_GIB - 0.1))
        assert p.memory_efficient is True
        assert p.sdnq_preset == "sdnq-uint8"

    def test_large_gpu_fp16(self):
        p = profile_from_specs(_specs(12.0))
        assert p.device == "cuda"
        assert p.memory_efficient is False
        assert p.cpu_offload is False

    def test_multi_gpu_uses_total_capacity_full(self):
        # 2x5 = 10 GiB → full profile
        p = profile_from_specs(_specs(5.0, count=2))
        assert p.device == "cuda"
        assert p.memory_efficient is False
        assert p.cpu_offload is False
        assert p.gpu_ids == [0, 1]
        assert p.total_vram_gib == 10.0

    def test_multi_gpu_each_small_still_mem_eff(self):
        p = profile_from_specs(_specs(2.0, count=2))
        assert p.memory_efficient is True
        assert p.sdnq_preset == "sdnq-uint8"

    def test_summary_contains_memory_efficient_when_active(self):
        p = profile_from_specs(_specs(5.0))
        assert "memory-efficient" in p.summary()
        assert "sdnq-uint8" in p.summary()

    def test_summary_omits_memory_efficient_when_inactive(self):
        p = profile_from_specs(_specs(12.0))
        assert "memory-efficient" not in p.summary()
        assert "FP16" in p.summary()


class TestDetectHardwareProfile:
    def test_returns_profile_instance(self, monkeypatch):
        monkeypatch.setattr("aigamekit_shared.hardware.cuda_gpu_specs", lambda: [])
        p = detect_hardware_profile()
        assert isinstance(p, Part3DHardwareProfile)
        assert p.device == "cpu"
