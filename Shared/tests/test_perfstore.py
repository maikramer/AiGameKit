"""Tests for aigamekit_shared.perfstore (db, models, recorder)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

from aigamekit_shared.perfstore.db import PerfDB
from aigamekit_shared.perfstore.models import GPUMeta, RunRecord, SpanRecord
from aigamekit_shared.perfstore.recorder import PerfRecorder


class TestModels:
    def test_gpu_meta_defaults(self):
        g = GPUMeta(device_name="RTX 4090", total_vram_mb=24564.0, compute_capability="8.9")
        assert g.driver_version == ""
        assert g.cuda_version == ""

    def test_run_record_defaults(self):
        r = RunRecord()
        assert r.id is None
        assert r.tool == ""
        assert r.success is True

    def test_span_record_defaults(self):
        s = SpanRecord()
        assert s.id is None
        assert s.run_id == 0
        assert s.cuda_allocated_before_mb is None


class TestPerfDB:
    def test_creates_db_file(self, tmp_path: Path):
        db_path = tmp_path / "perf.db"
        with PerfDB(db_path):
            assert db_path.is_file()

    def test_insert_and_read_run(self, tmp_path: Path):
        with PerfDB(tmp_path / "perf.db") as db:
            run = RunRecord(
                tool="text2d",
                started_at="2026-01-01T00:00:00Z",
                finished_at="2026-01-01T00:01:00Z",
                total_duration_ms=60000.0,
                gpu_name="RTX 4090",
                gpu_total_vram_mb=24564.0,
                quantization_mode="sdnq-uint8",
                model_id="flux-schnell",
            )
            run_id = db.insert_run(run)
            assert run_id >= 1

            runs = db.recent_runs()
            assert len(runs) == 1
            assert runs[0]["tool"] == "text2d"
            assert runs[0]["gpu_name"] == "RTX 4090"
            assert runs[0]["id"] == run_id

    def test_update_run_finish(self, tmp_path: Path):
        with PerfDB(tmp_path / "perf.db") as db:
            run = RunRecord(tool="text3d", started_at="2026-01-01T00:00:00Z")
            run_id = db.insert_run(run)

            db.update_run_finish(run_id, finished_at="2026-01-01T00:02:00Z", total_duration_ms=120000.0, success=True)

            runs = db.recent_runs()
            assert runs[0]["total_duration_ms"] == 120000.0
            assert runs[0]["success"] == 1

    def test_update_run_params(self, tmp_path: Path):
        with PerfDB(tmp_path / "perf.db") as db:
            run = RunRecord(tool="text2d", started_at="2026-01-01T00:00:00Z")
            run_id = db.insert_run(run)

            db.update_run_params(run_id, steps=50, guidance=7.5)
            runs = db.recent_runs()
            params = json.loads(runs[0]["params_json"])
            assert params["steps"] == 50
            assert params["guidance"] == 7.5

    def test_insert_and_read_spans(self, tmp_path: Path):
        with PerfDB(tmp_path / "perf.db") as db:
            run = RunRecord(tool="text2d", started_at="2026-01-01T00:00:00Z")
            run_id = db.insert_run(run)

            span = SpanRecord(
                run_id=run_id,
                span_name="generate",
                duration_ms=5000.0,
                cuda_allocated_before_mb=1000.0,
                cuda_allocated_after_mb=3000.0,
                cuda_allocated_delta_mb=2000.0,
                cuda_free_after_mb=500.0,
                cuda_total_mb=24564.0,
                rss_before_mb=500.0,
                rss_after_mb=800.0,
                rss_delta_mb=300.0,
            )
            span_id = db.insert_span(span)
            assert span_id >= 1

            spans = db.spans_for_run(run_id)
            assert len(spans) == 1
            assert spans[0]["span_name"] == "generate"
            assert spans[0]["cuda_allocated_after_mb"] == 3000.0

    def test_recent_runs_filter_by_tool(self, tmp_path: Path):
        with PerfDB(tmp_path / "perf.db") as db:
            db.insert_run(RunRecord(tool="text2d", started_at="2026-01-01T00:00:00Z"))
            db.insert_run(RunRecord(tool="text3d", started_at="2026-01-01T00:01:00Z"))
            db.insert_run(RunRecord(tool="text2d", started_at="2026-01-01T00:02:00Z"))

            runs = db.recent_runs(tool="text2d", limit=10)
            assert len(runs) == 2
            assert all(r["tool"] == "text2d" for r in runs)

    def test_recent_runs_limit(self, tmp_path: Path):
        with PerfDB(tmp_path / "perf.db") as db:
            for i in range(5):
                db.insert_run(RunRecord(tool="text2d", started_at=f"2026-01-01T00:0{i}:00Z"))

            runs = db.recent_runs(limit=3)
            assert len(runs) == 3

    def test_tool_summary(self, tmp_path: Path):
        with PerfDB(tmp_path / "perf.db") as db:
            db.insert_run(
                RunRecord(
                    tool="text2d",
                    started_at="2026-01-01T00:00:00Z",
                    total_duration_ms=5000.0,
                    gpu_name="RTX 4090",
                    gpu_total_vram_mb=24564.0,
                    quantization_mode="sdnq-uint8",
                    success=True,
                )
            )
            db.insert_run(
                RunRecord(
                    tool="text2d",
                    started_at="2026-01-01T00:01:00Z",
                    total_duration_ms=6000.0,
                    gpu_name="RTX 4090",
                    gpu_total_vram_mb=24564.0,
                    quantization_mode="sdnq-uint8",
                    success=True,
                )
            )

            rows = db.tool_summary(days=9999)
            assert len(rows) == 1
            assert rows[0]["run_count"] == 2
            assert rows[0]["avg_duration_ms"] == 5500.0

    def test_vram_by_quantization(self, tmp_path: Path):
        with PerfDB(tmp_path / "perf.db") as db:
            run = RunRecord(
                tool="text2d",
                started_at="2026-01-01T00:00:00Z",
                gpu_name="RTX 4090",
                gpu_total_vram_mb=24564.0,
                quantization_mode="sdnq-uint8",
                success=True,
            )
            run_id = db.insert_run(run)

            db.insert_span(
                SpanRecord(
                    run_id=run_id,
                    span_name="generate",
                    duration_ms=5000.0,
                    cuda_allocated_after_mb=3000.0,
                    cuda_free_after_mb=500.0,
                )
            )

            rows = db.vram_by_quantization(days=9999)
            assert len(rows) == 1
            assert rows[0]["peak_vram_mb"] == 3000.0

    def test_recommend_config(self, tmp_path: Path):
        with PerfDB(tmp_path / "perf.db") as db:
            for quant, vram_after in [("sdnq-uint4", 2000.0), ("sdnq-uint8", 4000.0), ("fp16", 8000.0)]:
                run = RunRecord(
                    tool="text2d",
                    started_at="2026-01-01T00:00:00Z",
                    gpu_name="RTX 4090",
                    gpu_total_vram_mb=24564.0,
                    quantization_mode=quant,
                    success=True,
                )
                run_id = db.insert_run(run)
                db.insert_span(
                    SpanRecord(
                        run_id=run_id,
                        span_name="generate",
                        duration_ms=5000.0,
                        cuda_allocated_after_mb=vram_after,
                        cuda_free_after_mb=24564.0 - vram_after,
                    )
                )

            rows = db.recommend_config("text2d", 5000.0, days=9999)
            assert len(rows) == 2
            quant_names = [r["quantization_mode"] for r in rows]
            assert "sdnq-uint4" in quant_names
            assert "sdnq-uint8" in quant_names
            assert "fp16" not in quant_names

    def test_delete_old_runs(self, tmp_path: Path):
        with PerfDB(tmp_path / "perf.db") as db:
            db.insert_run(RunRecord(tool="text2d", started_at="2020-01-01T00:00:00Z"))
            db.insert_run(RunRecord(tool="text2d", started_at="2020-01-02T00:00:00Z"))

            deleted = db.delete_old_runs(days=365)
            assert deleted == 2

            runs = db.recent_runs()
            assert len(runs) == 0

    def test_context_manager(self, tmp_path: Path):
        db_path = tmp_path / "perf.db"
        db = PerfDB(db_path)
        db.close()
        assert db_path.is_file()


class TestPerfRecorder:
    def test_creates_run_on_enter(self, tmp_path: Path):
        with PerfRecorder("test-tool", db=PerfDB(tmp_path / "perf.db")) as rec:
            assert rec.run_id is not None
            assert rec.run_id >= 1

    def test_span_records(self, tmp_path: Path):
        db_path = tmp_path / "perf.db"
        with PerfRecorder("test-tool", db=PerfDB(db_path)) as rec:
            with rec.span("warmup"):
                time.sleep(0.01)
            with rec.span("generate"):
                time.sleep(0.01)

        with PerfDB(db_path) as db:
            spans = db.spans_for_run(rec.run_id)  # type: ignore[arg-type]
            assert len(spans) == 2
            assert spans[0]["span_name"] == "warmup"
            assert spans[1]["span_name"] == "generate"
            assert spans[0]["duration_ms"] > 0

    def test_run_finished_on_exit(self, tmp_path: Path):
        db_path = tmp_path / "perf.db"
        with PerfRecorder("test-tool", db=PerfDB(db_path)):
            pass

        with PerfDB(db_path) as db:
            runs = db.recent_runs()
            assert len(runs) == 1
            assert runs[0]["tool"] == "test-tool"
            assert runs[0]["total_duration_ms"] >= 0
            assert runs[0]["success"] == 1

    def test_run_failure(self, tmp_path: Path):
        db_path = tmp_path / "perf.db"
        try:
            with PerfRecorder("test-tool", db=PerfDB(db_path)):
                raise RuntimeError("boom")
        except RuntimeError:
            pass

        with PerfDB(db_path) as db:
            runs = db.recent_runs()
            assert len(runs) == 1
            assert runs[0]["success"] == 0

    def test_quantization_and_model(self, tmp_path: Path):
        db_path = tmp_path / "perf.db"
        with PerfRecorder(
            "text2d",
            db=PerfDB(db_path),
            quantization_mode="sdnq-uint8",
            model_id="flux-schnell",
            params={"steps": 4},
        ) as _rec:
            pass

        with PerfDB(db_path) as db:
            runs = db.recent_runs()
            assert runs[0]["quantization_mode"] == "sdnq-uint8"
            assert runs[0]["model_id"] == "flux-schnell"
            params = json.loads(runs[0]["params_json"])
            assert params["steps"] == 4

    def test_update_params(self, tmp_path: Path):
        db_path = tmp_path / "perf.db"
        with PerfRecorder("text2d", db=PerfDB(db_path)) as rec:
            rec.update_params(guidance=7.5)

        with PerfDB(db_path) as db:
            runs = db.recent_runs()
            params = json.loads(runs[0]["params_json"])
            assert params["guidance"] == 7.5

    def test_own_db_lifecycle(self, tmp_path: Path):
        db_path = tmp_path / "auto_perf.db"
        with (
            patch("aigamekit_shared.perfstore.db.default_db_path", return_value=db_path),
            PerfRecorder("test-tool") as rec,
        ):
            assert rec.run_id is not None
        assert db_path.is_file()
