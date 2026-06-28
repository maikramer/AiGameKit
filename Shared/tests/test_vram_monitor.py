"""Tests for gamedev_shared.vram_monitor — VRAMStats aggregation and no-CUDA monitor paths."""

from __future__ import annotations

from unittest.mock import patch

from gamedev_shared.vram_monitor import VRAMMonitor, VRAMSnapshot, VRAMStats


def _snapshot(allocated: float, reserved: float, free: float, total: float = 8192.0) -> VRAMSnapshot:
    return VRAMSnapshot(
        timestamp=0.0,
        allocated_mb=allocated,
        reserved_mb=reserved,
        free_mb=free,
        total_mb=total,
    )


class TestVRAMStatsDefaults:
    def test_empty_defaults(self):
        stats = VRAMStats()
        assert stats.peak_allocated_mb == 0.0
        assert stats.peak_reserved_mb == 0.0
        assert stats.min_free_mb == float("inf")
        assert stats.avg_allocated_mb == 0.0
        assert stats.snapshots == []


class TestVRAMStatsAggregation:
    def test_add_snapshot_updates_peak(self):
        stats = VRAMStats()
        stats.add_snapshot(_snapshot(allocated=100, reserved=50, free=8092))
        stats.add_snapshot(_snapshot(allocated=400, reserved=200, free=7792))
        stats.add_snapshot(_snapshot(allocated=200, reserved=150, free=7992))
        assert stats.peak_allocated_mb == 400
        assert stats.peak_reserved_mb == 200

    def test_add_snapshot_updates_peak_reserved(self):
        stats = VRAMStats()
        stats.add_snapshot(_snapshot(allocated=10, reserved=500, free=7692))
        assert stats.peak_reserved_mb == 500

    def test_avg_recalculation(self):
        stats = VRAMStats()
        stats.add_snapshot(_snapshot(allocated=100, reserved=0, free=1))
        stats.add_snapshot(_snapshot(allocated=300, reserved=0, free=1))
        stats.add_snapshot(_snapshot(allocated=200, reserved=0, free=1))
        assert stats.avg_allocated_mb == 200.0

    def test_min_free_tracking(self):
        stats = VRAMStats()
        stats.add_snapshot(_snapshot(allocated=10, reserved=0, free=5000))
        stats.add_snapshot(_snapshot(allocated=10, reserved=0, free=200))
        stats.add_snapshot(_snapshot(allocated=10, reserved=0, free=3000))
        assert stats.min_free_mb == 200

    def test_min_free_stays_inf_when_higher(self):
        stats = VRAMStats()
        stats.add_snapshot(_snapshot(allocated=10, reserved=0, free=9999))
        assert stats.min_free_mb == 9999

    def test_snapshots_list_grows(self):
        stats = VRAMStats()
        stats.add_snapshot(_snapshot(allocated=1, reserved=0, free=1))
        stats.add_snapshot(_snapshot(allocated=2, reserved=0, free=1))
        assert len(stats.snapshots) == 2

    def test_single_snapshot_avg(self):
        stats = VRAMStats()
        stats.add_snapshot(_snapshot(allocated=250, reserved=10, free=7942))
        assert stats.avg_allocated_mb == 250.0


class TestVRAMMonitorNoCuda:
    def test_get_current_no_cuda_returns_none(self):
        monitor = VRAMMonitor()
        with patch("torch.cuda.is_available", return_value=False):
            assert monitor.get_current() is None

    def test_start_stop_without_cuda(self):
        monitor = VRAMMonitor(interval_sec=0.01)
        with patch("torch.cuda.is_available", return_value=False):
            monitor.start()
            stats = monitor.stop()
        assert isinstance(stats, VRAMStats)
        assert stats.snapshots == []

    def test_stop_without_start_returns_empty_stats(self):
        monitor = VRAMMonitor()
        stats = monitor.stop()
        assert isinstance(stats, VRAMStats)
        assert stats.snapshots == []

    def test_double_start_is_noop(self):
        monitor = VRAMMonitor(interval_sec=0.01)
        with patch("torch.cuda.is_available", return_value=False):
            monitor.start()
            monitor.start()
            stats = monitor.stop()
        assert isinstance(stats, VRAMStats)


class TestVRAMMonitorPrintSummary:
    def test_print_summary_empty(self, capsys):
        monitor = VRAMMonitor()
        monitor.print_summary()
        captured = capsys.readouterr()
        assert "Nenhum dado coletado" in captured.out

    def test_print_summary_with_data(self, capsys):
        monitor = VRAMMonitor()
        monitor._stats.add_snapshot(_snapshot(allocated=1000, reserved=500, free=7192, total=8192))
        monitor._stats.add_snapshot(_snapshot(allocated=2000, reserved=800, free=6192, total=8192))
        monitor.print_summary()
        captured = capsys.readouterr()
        assert "RESUMO DE VRAM" in captured.out
        assert "2000.0" in captured.out
        assert "Snapshots" in captured.out

    def test_print_summary_low_free_alert(self, capsys):
        monitor = VRAMMonitor()
        monitor._stats.add_snapshot(_snapshot(allocated=8000, reserved=8000, free=192, total=8192))
        monitor.print_summary()
        captured = capsys.readouterr()
        assert "ALERTA" in captured.out

    def test_print_summary_healthy(self, capsys):
        monitor = VRAMMonitor()
        monitor._stats.add_snapshot(_snapshot(allocated=100, reserved=50, free=8092, total=8192))
        monitor.print_summary()
        captured = capsys.readouterr()
        assert "saudáveis" in captured.out


class TestVRAMMonitorCallbacks:
    def test_callback_registered(self):
        monitor = VRAMMonitor()
        received: list[VRAMSnapshot] = []

        def cb(snap: VRAMSnapshot) -> None:
            received.append(snap)

        monitor.on_snapshot(cb)
        assert cb in monitor._callbacks
        assert len(monitor._callbacks) == 1
