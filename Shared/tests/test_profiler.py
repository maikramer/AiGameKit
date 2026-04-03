"""Testes para gamedev_shared.profiler (sem GPU obrigatória)."""

from __future__ import annotations

import json
from pathlib import Path

from gamedev_shared.profiler import (
    ProfilerSession,
    get_active_session,
    is_profiling_enabled,
    profile_span,
)
from gamedev_shared.profiler.snapshot import resource_snapshot


class TestProfilingEnabled:
    def test_cli_true(self):
        assert is_profiling_enabled(cli_flag=True) is True

    def test_cli_false_env_unset(self, monkeypatch):
        monkeypatch.delenv("GAMEDEV_PROFILE", raising=False)
        assert is_profiling_enabled(cli_flag=False) is False

    def test_env_one(self, monkeypatch):
        monkeypatch.setenv("GAMEDEV_PROFILE", "1")
        assert is_profiling_enabled(cli_flag=False) is True


class TestResourceSnapshot:
    def test_snapshot_runs(self):
        s = resource_snapshot()
        assert s.source in ("psutil", "rusage", "linux_proc", "none")


class TestProfilerSession:
    def test_disabled_no_events(self):
        with ProfilerSession("test", enabled=False) as s, s.span("a"):
            pass
        assert s.events == []

    def test_enabled_records_span(self, tmp_path: Path):
        log = tmp_path / "p.jsonl"
        with ProfilerSession("unit", enabled=True, log_path=log) as s, s.span("sleepy"):
            pass
        assert len(s.events) == 1
        ev = s.events[0]
        assert ev["tool"] == "unit"
        assert ev["span"] == "sleepy"
        assert "duration_ms" in ev
        assert "resource_before" in ev
        assert "resource_after" in ev
        assert log.is_file()
        line = log.read_text(encoding="utf-8").strip()
        data = json.loads(line)
        assert data["span"] == "sleepy"

    def test_profile_span_nested(self, tmp_path: Path):
        # ruff: noqa: SIM117
        with (
            ProfilerSession("unit", enabled=True, log_path=tmp_path / "x.jsonl") as s,
            s.span("outer"),
        ):
            with profile_span("inner"):
                pass
        names = [e["span"] for e in s.events]
        assert "outer" in names
        assert "inner" in names

    def test_get_active_session(self):
        with ProfilerSession("t", enabled=True) as s:
            assert get_active_session() is s
        assert get_active_session() is None
