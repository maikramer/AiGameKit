"""Tests for gamedev_shared.profiler.report and gamedev_shared.profiler.cuda."""

from __future__ import annotations

import io
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from gamedev_shared.profiler.cuda import (
    CudaMemorySnapshot,
    cuda_memory_snapshot,
    cuda_memory_snapshot_all,
    cuda_synchronize,
)
from gamedev_shared.profiler.report import (
    append_jsonl,
    print_gpu_summary,
    print_summary_table,
    utc_now_iso,
    write_jsonl_event,
)


class TestUtcNowIso:
    def test_returns_iso8601_parseable(self):
        ts = utc_now_iso()
        parsed = datetime.fromisoformat(ts)
        assert parsed is not None

    def test_includes_timezone(self):
        ts = utc_now_iso()
        parsed = datetime.fromisoformat(ts)
        assert parsed.tzinfo is not None


class TestWriteJsonlEvent:
    def test_writes_single_json_line_with_newline(self):
        buf = io.StringIO()
        write_jsonl_event(buf, {"span": "gen", "duration_ms": 42})
        content = buf.getvalue()
        assert content.endswith("\n")
        lines = content.splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0]) == {"span": "gen", "duration_ms": 42}

    def test_unicode_preserved(self):
        buf = io.StringIO()
        write_jsonl_event(buf, {"msg": "café — quantização"})
        parsed = json.loads(buf.getvalue())
        assert parsed["msg"] == "café — quantização"

    def test_non_ascii_not_escaped(self):
        buf = io.StringIO()
        write_jsonl_event(buf, {"x": "ü"})
        assert "\\u" not in buf.getvalue()


class TestAppendJsonl:
    def test_creates_parent_dirs_and_appends(self, tmp_path: Path):
        target = tmp_path / "nested" / "deep" / "events.jsonl"
        append_jsonl(target, {"i": 1})
        append_jsonl(target, {"i": 2})
        assert target.is_file()
        lines = target.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0]) == {"i": 1}
        assert json.loads(lines[1]) == {"i": 2}

    def test_append_does_not_clobber(self, tmp_path: Path):
        target = tmp_path / "events.jsonl"
        target.write_text(json.dumps({"existing": True}) + "\n", encoding="utf-8")
        append_jsonl(target, {"new": True})
        lines = target.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0]) == {"existing": True}
        assert json.loads(lines[1]) == {"new": True}

    def test_accepts_string_path(self, tmp_path: Path):
        target = str(tmp_path / "strpath.jsonl")
        append_jsonl(target, {"a": 1})
        lines = Path(target).read_text(encoding="utf-8").splitlines()
        assert json.loads(lines[0]) == {"a": 1}


class TestPrintSummaryTable:
    def test_empty_events_no_crash(self, capsys):
        print_summary_table([])
        captured = capsys.readouterr()
        assert "Nenhum evento registado" in captured.out

    def test_with_events_contains_span_and_duration(self, capsys):
        events = [
            {"span": "warmup", "duration_ms": 10, "rss_delta_mb": 5},
            {"span": "generate", "duration_ms": 500, "rss_delta_mb": 300, "cuda_allocated_delta_mb": 2000},
        ]
        print_summary_table(events)
        captured = capsys.readouterr()
        assert "warmup" in captured.out
        assert "generate" in captured.out
        assert "500" in captured.out

    def test_missing_optional_fields_use_dash(self, capsys):
        events = [{"span": "only_span", "duration_ms": 1}]
        print_summary_table(events)
        captured = capsys.readouterr()
        assert "only_span" in captured.out

    def test_writes_to_custom_file(self):
        buf = io.StringIO()
        print_summary_table([{"span": "x", "duration_ms": 7}], file=buf)
        assert "x" in buf.getvalue()


class TestPrintGpuSummary:
    def test_empty_events_no_output(self, capsys):
        print_gpu_summary([])
        assert capsys.readouterr().out == ""

    def test_events_without_gpu_fields_no_output(self, capsys):
        print_gpu_summary([{"span": "a", "duration_ms": 1}])
        assert capsys.readouterr().out == ""

    def test_events_with_gpu_fields_print_rows(self, capsys):
        events = [
            {
                "span": "gen",
                "cuda_after": {
                    "cuda_available": True,
                    "cuda_device": 0,
                    "cuda_allocated_mb": 3000,
                    "cuda_peak_allocated_mb": 3500,
                    "cuda_device_name": "RTX 4090",
                },
            },
        ]
        print_gpu_summary(events)
        captured = capsys.readouterr()
        assert "RTX 4090" in captured.out
        assert "VRAM" in captured.out

    def test_multi_gpu_events(self, capsys):
        events = [
            {
                "span": "gen",
                "cuda_all": [
                    {
                        "cuda_available": True,
                        "cuda_device": 0,
                        "cuda_allocated_mb": 2000,
                        "cuda_device_name": "GPU0",
                    },
                    {
                        "cuda_available": True,
                        "cuda_device": 1,
                        "cuda_allocated_mb": 4000,
                        "cuda_device_name": "GPU1",
                    },
                ],
            },
        ]
        print_gpu_summary(events)
        captured = capsys.readouterr()
        assert "GPU0" in captured.out
        assert "GPU1" in captured.out

    def test_unavailable_cuda_skipped(self, capsys):
        events = [
            {
                "span": "gen",
                "cuda_after": {"cuda_available": False},
            },
        ]
        print_gpu_summary(events)
        assert capsys.readouterr().out == ""


class TestCudaMemorySnapshotToDict:
    def test_unavailable_snapshot(self):
        snap = CudaMemorySnapshot(
            available=False,
            device_index=None,
            device_name=None,
            allocated_bytes=None,
            reserved_bytes=None,
            peak_allocated_bytes=None,
            free_bytes=None,
            total_bytes=None,
        )
        assert snap.to_dict() == {"cuda_available": False}

    def test_available_full_round_trip(self):
        snap = CudaMemorySnapshot(
            available=True,
            device_index=0,
            device_name="RTX 4090",
            allocated_bytes=1048576,
            reserved_bytes=2097152,
            peak_allocated_bytes=3145728,
            free_bytes=100 * 1024 * 1024,
            total_bytes=200 * 1024 * 1024,
        )
        d = snap.to_dict()
        assert d["cuda_available"] is True
        assert d["cuda_device"] == 0
        assert d["cuda_device_name"] == "RTX 4090"
        assert d["cuda_allocated_mb"] == 1.0
        assert d["cuda_reserved_mb"] == 2.0
        assert d["cuda_peak_allocated_mb"] == 3.0
        assert d["cuda_free_mb"] == 100.0
        assert d["cuda_total_mb"] == 200.0

    def test_free_and_total_emitted_together(self):
        snap = CudaMemorySnapshot(
            available=True,
            device_index=0,
            device_name=None,
            allocated_bytes=None,
            reserved_bytes=None,
            peak_allocated_bytes=None,
            free_bytes=50 * 1024 * 1024,
            total_bytes=100 * 1024 * 1024,
        )
        d = snap.to_dict()
        assert "cuda_free_mb" in d
        assert "cuda_total_mb" in d

    def test_free_without_total_omitted(self):
        snap = CudaMemorySnapshot(
            available=True,
            device_index=0,
            device_name=None,
            allocated_bytes=None,
            reserved_bytes=None,
            peak_allocated_bytes=None,
            free_bytes=50 * 1024 * 1024,
            total_bytes=None,
        )
        d = snap.to_dict()
        assert "cuda_free_mb" not in d
        assert "cuda_total_mb" not in d

    def test_rounding_to_three_decimals(self):
        snap = CudaMemorySnapshot(
            available=True,
            device_index=0,
            device_name="x",
            allocated_bytes=1572864,
            reserved_bytes=None,
            peak_allocated_bytes=None,
            free_bytes=None,
            total_bytes=None,
        )
        assert snap.to_dict()["cuda_allocated_mb"] == 1.5


class TestCudaMemorySnapshotFunc:
    def test_no_cuda_returns_unavailable(self):
        with patch("torch.cuda.is_available", return_value=False):
            snap = cuda_memory_snapshot()
        assert snap.available is False
        assert snap.device_index is None
        assert snap.to_dict() == {"cuda_available": False}

    def test_no_cuda_returns_empty_list_all(self):
        with patch("torch.cuda.is_available", return_value=False):
            assert cuda_memory_snapshot_all() == []


class TestCudaSynchronize:
    def test_no_cuda_no_exception(self):
        with patch("torch.cuda.is_available", return_value=False):
            cuda_synchronize()

    def test_cuda_available_calls_synchronize(self):
        with (
            patch("torch.cuda.is_available", return_value=True),
            patch("torch.cuda.synchronize") as mock_sync,
        ):
            cuda_synchronize(0)
        mock_sync.assert_called_once_with(0)
