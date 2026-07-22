"""Testes para gamedev_shared.gpu (funções sem dependência de GPU real)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import gamedev_shared.gpu as gpu_module
from gamedev_shared.gpu import (
    estimate_vram_requirement,
    format_bytes,
    warn_if_vram_occupied,
)


class TestFormatBytes:
    def test_bytes(self):
        assert format_bytes(500) == "500.0 B"

    def test_kilobytes(self):
        assert format_bytes(1024) == "1.0 KB"

    def test_megabytes(self):
        assert format_bytes(1024 * 1024) == "1.0 MB"

    def test_gigabytes(self):
        assert format_bytes(1024**3) == "1.0 GB"

    def test_terabytes(self):
        assert format_bytes(1024**4) == "1.0 TB"

    def test_fractional(self):
        result = format_bytes(int(4.5 * 1024**3))
        assert "GB" in result

    def test_zero(self):
        assert format_bytes(0) == "0.0 B"


class TestEstimateVram:
    def test_default(self):
        est = estimate_vram_requirement()
        assert est > 0
        assert est == pytest.approx(4.9 * 1.2, rel=0.01)

    def test_larger_frame(self):
        base = estimate_vram_requirement(frame_size=256)
        larger = estimate_vram_requirement(frame_size=512)
        assert larger > base

    def test_batch_scales(self):
        single = estimate_vram_requirement(batch_size=1)
        double = estimate_vram_requirement(batch_size=2)
        assert double == pytest.approx(single * 2, rel=0.01)


class TestProcessVramMib:
    def test_sums_matching_pid(self, monkeypatch):
        monkeypatch.setattr(
            gpu_module,
            "list_nvidia_compute_apps",
            lambda: [(111, "python", 400), (111, "python", 200), (222, "other", 999)],
        )
        assert gpu_module.process_vram_mib(111) == 600

    def test_none_when_missing(self, monkeypatch):
        monkeypatch.setattr(gpu_module, "list_nvidia_compute_apps", lambda: [(1, "x", 10)])
        assert gpu_module.process_vram_mib(999) is None


class TestClearCudaMemoryIpc:
    def test_calls_ipc_collect_when_present(self, monkeypatch):
        calls: list[str] = []

        class _Cuda:
            @staticmethod
            def is_available() -> bool:
                return True

            @staticmethod
            def synchronize() -> None:
                calls.append("sync")

            @staticmethod
            def empty_cache() -> None:
                calls.append("empty")

            @staticmethod
            def ipc_collect() -> None:
                calls.append("ipc")

        fake_torch = SimpleNamespace(cuda=_Cuda())
        monkeypatch.setattr(gpu_module, "_torch", lambda: fake_torch)
        gpu_module.clear_cuda_memory()
        assert "empty" in calls
        assert "ipc" in calls
        assert "sync" in calls


class TestWarnIfVramOccupied:
    def test_no_warning_when_empty(self, monkeypatch):
        monkeypatch.setattr(gpu_module, "list_nvidia_compute_apps", lambda: [])
        result = warn_if_vram_occupied()
        assert result == []

    def test_warning_when_occupied(self, monkeypatch, capsys):
        monkeypatch.setattr(
            gpu_module,
            "list_nvidia_compute_apps",
            lambda: [(12345, "python", 2048)],
        )
        result = warn_if_vram_occupied(threshold_mib=1024)
        assert len(result) == 1
        assert "12345" in result[0]

    def test_below_threshold_no_warning(self, monkeypatch):
        monkeypatch.setattr(
            gpu_module,
            "list_nvidia_compute_apps",
            lambda: [(12345, "python", 512)],
        )
        result = warn_if_vram_occupied(threshold_mib=1024)
        assert result == []

    def test_null_mib_ignored(self, monkeypatch):
        monkeypatch.setattr(
            gpu_module,
            "list_nvidia_compute_apps",
            lambda: [(12345, "python", None)],
        )
        result = warn_if_vram_occupied(threshold_mib=1024)
        assert result == []


class TestNvmlBytesToMib:
    def test_normal(self):
        assert gpu_module._nvml_bytes_to_mib(2 * 1024 * 1024) == 2

    def test_unavailable_sentinel(self):
        assert gpu_module._nvml_bytes_to_mib(gpu_module._NVML_VALUE_NOT_AVAILABLE) is None

    def test_none(self):
        assert gpu_module._nvml_bytes_to_mib(None) is None


class TestQueryGpuFreeMib:
    def test_prefers_nvml(self, monkeypatch):
        monkeypatch.setattr(gpu_module, "_nvml_memory_mib", lambda device=0: (4096, 8192, 4096))
        monkeypatch.setattr(
            gpu_module,
            "_smi_query_free_mib",
            lambda device=0: (_ for _ in ()).throw(AssertionError("smi não deve ser chamado")),
        )
        assert gpu_module.query_gpu_free_mib() == 4096

    def test_falls_back_to_smi(self, monkeypatch):
        monkeypatch.setattr(gpu_module, "_nvml_memory_mib", lambda device=0: None)
        monkeypatch.setattr(gpu_module, "_smi_query_free_mib", lambda device=0: 1234)
        assert gpu_module.query_gpu_free_mib(1) == 1234

    def test_none_when_both_fail(self, monkeypatch):
        monkeypatch.setattr(gpu_module, "_nvml_memory_mib", lambda device=0: None)
        monkeypatch.setattr(gpu_module, "_smi_query_free_mib", lambda device=0: None)
        assert gpu_module.query_gpu_free_mib() is None


class TestDetectGpuIds:
    def test_prefers_nvml_count(self, monkeypatch):
        monkeypatch.setattr(gpu_module, "_nvml_device_count", lambda: 2)
        monkeypatch.setattr(
            gpu_module,
            "_smi_detect_gpu_ids",
            lambda: (_ for _ in ()).throw(AssertionError("smi não deve")),
        )
        assert gpu_module.detect_gpu_ids() == [0, 1]

    def test_falls_back_to_smi(self, monkeypatch):
        monkeypatch.setattr(gpu_module, "_nvml_device_count", lambda: None)
        monkeypatch.setattr(gpu_module, "_smi_detect_gpu_ids", lambda: [0])
        assert gpu_module.detect_gpu_ids() == [0]


class TestListNvidiaComputeApps:
    def test_prefers_nvml(self, monkeypatch):
        monkeypatch.setattr(
            gpu_module,
            "_nvml_list_compute_apps",
            lambda: [(42, "python", 100)],
        )
        monkeypatch.setattr(
            gpu_module,
            "_smi_list_compute_apps",
            lambda: (_ for _ in ()).throw(AssertionError("smi não")),
        )
        assert gpu_module.list_nvidia_compute_apps() == [(42, "python", 100)]

    def test_falls_back_to_smi(self, monkeypatch):
        monkeypatch.setattr(gpu_module, "_nvml_list_compute_apps", lambda: None)
        monkeypatch.setattr(gpu_module, "_smi_list_compute_apps", lambda: [(7, "torch", 50)])
        assert gpu_module.list_nvidia_compute_apps() == [(7, "torch", 50)]


class TestQueryGpuSnapshot:
    def test_nvml_snapshot(self, monkeypatch):
        monkeypatch.setattr(gpu_module, "_nvml_memory_mib", lambda device=0: (1000, 8000, 7000))
        monkeypatch.setattr(gpu_module, "_nvml_device_name", lambda device=0: "RTX Test")
        snap = gpu_module.query_gpu_snapshot(0)
        assert snap is not None
        assert snap.source == "nvml"
        assert snap.name == "RTX Test"
        assert snap.free_mib == 1000
        assert snap.total_mib == 8000
        assert snap.used_mib == 7000

    def test_smi_fallback(self, monkeypatch):
        monkeypatch.setattr(gpu_module, "_nvml_memory_mib", lambda device=0: None)
        monkeypatch.setattr(
            gpu_module,
            "_smi_gpu_snapshot",
            lambda device=0: gpu_module.GpuSnapshot(
                index=0,
                name="Fake",
                free_mib=10,
                total_mib=20,
                used_mib=10,
                source="nvidia-smi",
            ),
        )
        snap = gpu_module.query_gpu_snapshot()
        assert snap is not None
        assert snap.source == "nvidia-smi"


class TestNvmlComputeAppsForDevice:
    def test_parses_process_info(self, monkeypatch):
        handle = object()
        monkeypatch.setattr(gpu_module, "_nvml_handle", lambda device: handle)
        monkeypatch.setattr(gpu_module, "_process_basename", lambda pid: f"proc-{pid}")

        fake_pynvml = MagicMock()
        fake_pynvml.nvmlDeviceGetComputeRunningProcesses_v3.return_value = [
            SimpleNamespace(pid=99, usedGpuMemory=5 * 1024 * 1024),
        ]
        monkeypatch.setitem(__import__("sys").modules, "pynvml", fake_pynvml)
        # Force import path inside function to see our mock — patch import via sys.modules
        apps = gpu_module._nvml_compute_apps_for_device(0)
        assert apps == [(99, "proc-99", 5)]
