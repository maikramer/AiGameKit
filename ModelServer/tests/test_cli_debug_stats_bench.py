"""CLI UMS: debug / stats / bench — só leitura (nunca stop/flush)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from click.testing import CliRunner
from modelserver.cli import _budget_short, _percentile, cli
from modelserver.server import UnifiedModelServer
from modelserver.stats import StatsCollector


class TestBudgetShort:
    def test_chunks(self) -> None:
        assert "num_chunks=10" in _budget_short({"num_chunks": 10, "x": 1})

    def test_empty(self) -> None:
        assert _budget_short(None) == "—"
        assert _budget_short({}) == "—"


class TestPercentile:
    def test_p50(self) -> None:
        assert _percentile([1.0, 2.0, 3.0], 50) == 2.0

    def test_empty(self) -> None:
        assert _percentile([], 95) is None


class TestStatsResetProtocol:
    def test_reset_clears_without_shutdown(self, tmp_path: Path) -> None:
        from modelserver.registry import Registry

        sock = tmp_path / "ums-test-stats-reset.sock"
        srv = UnifiedModelServer(registry=Registry(), socket_path=sock)
        srv.manager.stats.record_generate("text2icon", 1.5)
        assert srv.manager.stats.get("text2icon") is not None
        running_before = srv._running
        out = srv._dispatch({"cmd": "stats", "reset": True})
        assert out["status"] == "ok"
        assert out.get("reset") is True
        assert srv.manager.stats.get_all() == {}
        # Reset NÃO mexe em _running (não é shutdown).
        assert srv._running is running_before


class TestStatsResponseDebug:
    def test_stats_includes_debug_budgets(self, tmp_path: Path) -> None:
        from modelserver.registry import Registry

        sock = tmp_path / "ums-test-stats-dbg.sock"
        srv = UnifiedModelServer(registry=Registry(), socket_path=sock)
        srv.manager.stats.record_generate("text3d", 2.0)
        srv.manager.stats.record_runtime_budget("text3d", {"num_chunks": 99})
        out = srv._dispatch({"cmd": "stats"})
        assert out["status"] == "ok"
        assert "queue_metrics" in out
        assert out["backends"]["text3d"]["last_runtime_budget"]["num_chunks"] == 99
        assert out["debug"]["last_runtime_budgets"]["text3d"]["num_chunks"] == 99


def _fake_send_factory(responses: dict[str, dict[str, Any]]):
    def _send(request: dict, *, timeout: float = 30.0) -> dict | None:
        cmd = request.get("cmd")
        if request.get("reset") and cmd == "stats":
            return responses.get("stats_reset") or {
                "status": "ok",
                "reset": True,
                "message": "stats reset (jobs/backends intactos)",
                "pid": 1,
                "backends": {},
                "queue_metrics": {},
            }
        return responses.get(str(cmd))

    return _send


class TestCliStatsDebugBench:
    def test_stats_json(self) -> None:
        runner = CliRunner()
        fake = {
            "stats": {
                "status": "ok",
                "pid": 42,
                "requests_served": 3,
                "idle_evict_timeout_sec": 600,
                "max_inflight": 1,
                "max_affinity_cuts": 3,
                "queue": {"queue_depth": 0, "inflight": 0},
                "queue_metrics": {"enqueued": 3, "completed": 3},
                "affinity_hits": 1,
                "backends": {
                    "text2icon": {
                        "load_count": 1,
                        "generate_count": 2,
                        "evict_count": 0,
                        "error_count": 0,
                        "avg_load_time_sec": 1.0,
                        "avg_generate_time_sec": 0.5,
                        "last_generate_time_sec": 0.4,
                        "idle_sec": 10,
                        "last_runtime_budget": None,
                    }
                },
                "debug": {"last_errors": {}},
            }
        }
        with patch("modelserver.cli._send", side_effect=_fake_send_factory(fake)):
            result = runner.invoke(cli, ["stats", "--json"])
        assert result.exit_code == 0
        assert '"pid": 42' in result.output or '"pid":42' in result.output.replace(" ", "")

    def test_stats_reset_flag(self) -> None:
        runner = CliRunner()
        with patch("modelserver.cli._send", side_effect=_fake_send_factory({})):
            result = runner.invoke(cli, ["stats", "--reset"])
        assert result.exit_code == 0
        assert "reset" in result.output.lower() or "intactos" in result.output.lower()

    def test_debug_shows_holding(self) -> None:
        runner = CliRunner()
        fake = {
            "status": {
                "status": "status",
                "pid": 7,
                "queue": {"queue_depth": 1, "inflight": 1},
                "queue_metrics": {"enqueued": 2},
                "debug": {"loaded_backends": ["text3d"], "affinity_hits": 2, "last_errors": {}},
                "eta_sec": 12,
            },
            "queue": {
                "status": "ok",
                "queue_depth": 1,
                "inflight": 1,
                "running": [{"job_id": "abcdef1234567890", "backend": "text3d", "priority": "batch"}],
                "queued": [],
            },
            "stats": {"status": "ok", "backends": {}, "queue_metrics": {}, "debug": {}},
        }
        with patch("modelserver.cli._send", side_effect=_fake_send_factory(fake)):
            result = runner.invoke(cli, ["debug"])
        assert result.exit_code == 0
        assert "HOLDING" in result.output or "text3d" in result.output
        assert "stop" not in result.output.lower() or "Só leitura" in result.output

    def test_bench_ipc_only(self) -> None:
        runner = CliRunner()
        calls: list[str] = []

        def _send(request: dict, *, timeout: float = 30.0) -> dict | None:
            cmd = str(request.get("cmd"))
            calls.append(cmd)
            if cmd == "queue":
                return {"status": "ok", "queue_depth": 0, "inflight": 0, "running": [], "queued": []}
            return {"status": "ok", "pid": 1}

        with patch("modelserver.cli._send", side_effect=_send):
            result = runner.invoke(cli, ["bench", "--rounds", "3", "--cmds", "status,queue"])
        assert result.exit_code == 0
        assert "RTT" in result.output or "status" in result.output
        assert "generate" not in calls
        assert "submit" not in calls
        assert "shutdown" not in calls
        assert "flush" not in calls
        assert "release" not in calls

    def test_bench_rejects_generate_cmd(self) -> None:
        runner = CliRunner()
        with patch("modelserver.cli._send", return_value={"status": "ok", "queue_depth": 0, "inflight": 0}):
            result = runner.invoke(cli, ["bench", "--cmds", "generate"])
        assert result.exit_code != 0


class TestStatsCollectorReset:
    def test_reset_clears_queue_too(self) -> None:
        c = StatsCollector()
        c.record_enqueue(depth_after=2)
        c.record_generate("x", 1.0)
        c.reset()
        assert c.get_all() == {}
        assert c.queue_dict()["enqueued"] == 0
