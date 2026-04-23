"""Testes para gamedev_shared.gpu (funções sem dependência de GPU real)."""

import pytest

import gamedev_shared.gpu as gpu_module
from gamedev_shared.gpu import estimate_vram_requirement, format_bytes, warn_if_vram_occupied


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
