"""Testes das estatísticas do UMS — BackendStats + StatsCollector."""

from __future__ import annotations

import time

from modelserver.stats import BackendStats, StatsCollector


class TestBackendStats:
    """BackendStats dataclass — propriedades derivadas."""

    def test_avg_load_time_zero_when_no_loads(self) -> None:
        s = BackendStats()
        assert s.avg_load_time_sec == 0.0

    def test_avg_load_time_with_data(self) -> None:
        s = BackendStats(load_count=3, total_load_time_sec=30.0)
        assert s.avg_load_time_sec == 10.0

    def test_avg_generate_time_zero_when_no_gens(self) -> None:
        s = BackendStats()
        assert s.avg_generate_time_sec == 0.0

    def test_to_dict_includes_all_fields(self) -> None:
        s = BackendStats(load_count=2, generate_count=5, last_used_at=time.monotonic())
        d = s.to_dict()
        assert d["load_count"] == 2
        assert d["generate_count"] == 5
        assert "idle_sec" in d
        assert "avg_load_time_sec" in d
        assert "avg_generate_time_sec" in d


class TestStatsCollector:
    """StatsCollector — thread-safe tracking."""

    def test_record_load(self) -> None:
        c = StatsCollector()
        c.record_load("text2icon", 15.5)
        s = c.get("text2icon")
        assert s is not None
        assert s.load_count == 1
        assert s.total_load_time_sec == 15.5
        assert s.last_load_time_sec == 15.5
        assert s.first_loaded_at > 0

    def test_record_generate(self) -> None:
        c = StatsCollector()
        c.record_generate("text2icon", 3.2)
        s = c.get("text2icon")
        assert s is not None
        assert s.generate_count == 1
        assert s.total_generate_time_sec == 3.2

    def test_record_evict(self) -> None:
        c = StatsCollector()
        c.record_evict("text2icon")
        s = c.get("text2icon")
        assert s is not None
        assert s.evict_count == 1

    def test_record_error(self) -> None:
        c = StatsCollector()
        c.record_error("text2icon", "OOM")
        s = c.get("text2icon")
        assert s is not None
        assert s.error_count == 1
        assert s.last_error == "OOM"

    def test_record_runtime_budget(self) -> None:
        c = StatsCollector()
        c.record_runtime_budget("text3d", {"num_chunks": 262144, "free_vram_bytes": 4 * 1024**3})
        s = c.get("text3d")
        assert s is not None
        assert s.last_runtime_budget == {"num_chunks": 262144, "free_vram_bytes": 4 * 1024**3}
        assert s.to_dict()["last_runtime_budget"]["num_chunks"] == 262144

    def test_record_runtime_budget_ignores_empty(self) -> None:
        c = StatsCollector()
        c.record_runtime_budget("text3d", None)
        c.record_runtime_budget("text3d", {})
        assert c.get("text3d") is None

    def test_record_runtime_budget_overwrites_last(self) -> None:
        c = StatsCollector()
        c.record_runtime_budget("paint3d", {"max_views": 6})
        c.record_runtime_budget("paint3d", {"max_views": 4})
        s = c.get("paint3d")
        assert s is not None
        assert s.last_runtime_budget == {"max_views": 4}

    def test_multiple_loads_accumulate(self) -> None:
        c = StatsCollector()
        c.record_load("text2icon", 10.0)
        c.record_load("text2icon", 20.0)
        s = c.get("text2icon")
        assert s is not None
        assert s.load_count == 2
        assert s.total_load_time_sec == 30.0
        assert s.avg_load_time_sec == 15.0
        assert s.last_load_time_sec == 20.0

    def test_get_nonexistent_returns_none(self) -> None:
        c = StatsCollector()
        assert c.get("nonexistent") is None

    def test_get_all_returns_serialized(self) -> None:
        c = StatsCollector()
        c.record_load("alpha", 5.0)
        c.record_generate("beta", 2.0)
        all_stats = c.get_all()
        assert "alpha" in all_stats
        assert "beta" in all_stats
        assert all_stats["alpha"]["load_count"] == 1
        assert all_stats["beta"]["generate_count"] == 1

    def test_reset_clears_all(self) -> None:
        c = StatsCollector()
        c.record_load("alpha", 5.0)
        c.reset()
        assert c.get("alpha") is None
        assert c.get_all() == {}

    def test_last_used_updated_on_load_and_generate(self) -> None:
        c = StatsCollector()
        c.record_load("alpha", 1.0)
        first = c.get("alpha").last_used_at
        time.sleep(0.01)
        c.record_generate("alpha", 0.5)
        second = c.get("alpha").last_used_at
        assert second > first
