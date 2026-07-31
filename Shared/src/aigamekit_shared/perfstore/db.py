"""SQLite backend for performance storage."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from .models import RunRecord, SpanRecord

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tool            TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    finished_at     TEXT NOT NULL DEFAULT '',
    total_duration_ms REAL NOT NULL DEFAULT 0,
    success         INTEGER NOT NULL DEFAULT 1,
    gpu_name        TEXT NOT NULL DEFAULT '',
    gpu_total_vram_mb REAL NOT NULL DEFAULT 0,
    gpu_compute_cap TEXT NOT NULL DEFAULT '',
    hostname        TEXT NOT NULL DEFAULT '',
    python_version  TEXT NOT NULL DEFAULT '',
    pytorch_version TEXT NOT NULL DEFAULT '',
    cuda_version    TEXT NOT NULL DEFAULT '',
    quantization_mode TEXT NOT NULL DEFAULT '',
    model_id        TEXT NOT NULL DEFAULT '',
    params_json     TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS spans (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                  INTEGER NOT NULL,
    span_name               TEXT NOT NULL,
    duration_ms             REAL NOT NULL DEFAULT 0,
    cuda_allocated_before_mb REAL,
    cuda_allocated_after_mb  REAL,
    cuda_allocated_delta_mb  REAL,
    cuda_reserved_before_mb  REAL,
    cuda_reserved_after_mb   REAL,
    cuda_peak_after_mb       REAL,
    cuda_free_after_mb       REAL,
    cuda_total_mb            REAL,
    rss_before_mb            REAL,
    rss_after_mb             REAL,
    rss_delta_mb             REAL,
    cpu_user_delta_s         REAL,
    cpu_system_delta_s       REAL,
    parent_tool             TEXT NOT NULL DEFAULT '',
    extra_json              TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (run_id) REFERENCES runs(id)
);

CREATE INDEX IF NOT EXISTS idx_runs_tool ON runs(tool);
CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at);
CREATE INDEX IF NOT EXISTS idx_runs_gpu ON runs(gpu_name, gpu_total_vram_mb);
CREATE INDEX IF NOT EXISTS idx_runs_quant ON runs(quantization_mode);
CREATE INDEX IF NOT EXISTS idx_spans_run ON spans(run_id);
CREATE INDEX IF NOT EXISTS idx_spans_name ON spans(span_name);
"""


def default_db_path() -> Path:
    """Return the default perf DB path, honoring ``AIGAMEKIT_PERF_DB``."""
    env = os.environ.get("AIGAMEKIT_PERF_DB", "").strip()
    if env:
        return Path(env)
    xdg = os.environ.get("XDG_CACHE_HOME", "").strip()
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "aigamekit" / "perf.db"


class PerfDB:
    """Thread-safe SQLite interface for performance records."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> PerfDB:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def insert_run(self, run: RunRecord) -> int:
        """Insert a run and return its auto-generated ``id``."""
        cur = self._conn.execute(
            """INSERT INTO runs
               (tool, started_at, finished_at, total_duration_ms, success,
                gpu_name, gpu_total_vram_mb, gpu_compute_cap,
                hostname, python_version, pytorch_version, cuda_version,
                quantization_mode, model_id, params_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                run.tool,
                run.started_at,
                run.finished_at,
                run.total_duration_ms,
                int(run.success),
                run.gpu_name,
                run.gpu_total_vram_mb,
                run.gpu_compute_cap,
                run.hostname,
                run.python_version,
                run.pytorch_version,
                run.cuda_version,
                run.quantization_mode,
                run.model_id,
                run.params_json,
            ),
        )
        self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def update_run_finish(self, run_id: int, *, finished_at: str, total_duration_ms: float, success: bool) -> None:
        self._conn.execute(
            "UPDATE runs SET finished_at=?, total_duration_ms=?, success=? WHERE id=?",
            (finished_at, total_duration_ms, int(success), run_id),
        )
        self._conn.commit()

    def update_run_params(self, run_id: int, **params: Any) -> None:
        """Merge extra params into the run's ``params_json``."""
        row = self._conn.execute("SELECT params_json FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            return
        existing = json.loads(row[0]) if row[0] else {}
        existing.update(params)
        self._conn.execute(
            "UPDATE runs SET params_json=?, quantization_mode=?, model_id=? WHERE id=?",
            (
                json.dumps(existing, ensure_ascii=False),
                existing.get("quantization_mode", ""),
                existing.get("model_id", ""),
                run_id,
            ),
        )
        self._conn.commit()

    def insert_span(self, span: SpanRecord) -> int:
        cur = self._conn.execute(
            """INSERT INTO spans
               (run_id, span_name, duration_ms,
                cuda_allocated_before_mb, cuda_allocated_after_mb, cuda_allocated_delta_mb,
                cuda_reserved_before_mb, cuda_reserved_after_mb,
                cuda_peak_after_mb, cuda_free_after_mb, cuda_total_mb,
                rss_before_mb, rss_after_mb, rss_delta_mb,
                cpu_user_delta_s, cpu_system_delta_s,
                parent_tool, extra_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                span.run_id,
                span.span_name,
                span.duration_ms,
                span.cuda_allocated_before_mb,
                span.cuda_allocated_after_mb,
                span.cuda_allocated_delta_mb,
                span.cuda_reserved_before_mb,
                span.cuda_reserved_after_mb,
                span.cuda_peak_after_mb,
                span.cuda_free_after_mb,
                span.cuda_total_mb,
                span.rss_before_mb,
                span.rss_after_mb,
                span.rss_delta_mb,
                span.cpu_user_delta_s,
                span.cpu_system_delta_s,
                span.parent_tool,
                span.extra_json,
            ),
        )
        self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def recent_runs(self, *, tool: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent runs as dicts, newest first."""
        if tool:
            rows = self._conn.execute(
                "SELECT * FROM runs WHERE tool=? ORDER BY id DESC LIMIT ?",
                (tool, limit),
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        cols = [d[0] for d in self._conn.execute("SELECT * FROM runs LIMIT 0").description]
        return [dict(zip(cols, r, strict=True)) for r in rows]

    def spans_for_run(self, run_id: int) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT * FROM spans WHERE run_id=? ORDER BY id", (run_id,)).fetchall()
        cols = [d[0] for d in self._conn.execute("SELECT * FROM spans LIMIT 0").description]
        return [dict(zip(cols, r, strict=True)) for r in rows]

    def tool_summary(
        self,
        *,
        tool: str | None = None,
        gpu_name: str | None = None,
        quantization_mode: str | None = None,
        days: int = 30,
    ) -> list[dict[str, Any]]:
        """Aggregate summary: avg/max/min duration and VRAM per tool+quantization."""
        conditions: list[str] = []
        params: list[Any] = []

        conditions.append("started_at >= datetime('now', ?)")
        params.append(f"-{days} days")

        if tool:
            conditions.append("tool=?")
            params.append(tool)
        if gpu_name:
            conditions.append("gpu_name LIKE ?")
            params.append(f"%{gpu_name}%")
        if quantization_mode:
            conditions.append("quantization_mode=?")
            params.append(quantization_mode)

        where = " AND ".join(conditions) if conditions else "1=1"

        query = f"""\
            SELECT
                tool,
                quantization_mode,
                gpu_name,
                gpu_total_vram_mb,
                COUNT(*) as run_count,
                ROUND(AVG(total_duration_ms), 1) as avg_duration_ms,
                ROUND(MIN(total_duration_ms), 1) as min_duration_ms,
                ROUND(MAX(total_duration_ms), 1) as max_duration_ms,
                SUM(CASE WHEN success=1 THEN 1 ELSE 0 END) as success_count,
                SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) as fail_count
            FROM runs
            WHERE {where}
            GROUP BY tool, quantization_mode, gpu_name, gpu_total_vram_mb
            ORDER BY tool, avg_duration_ms"""

        cur = self._conn.execute(query, params)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in rows]

    def vram_by_quantization(
        self,
        *,
        tool: str | None = None,
        gpu_name: str | None = None,
        days: int = 30,
    ) -> list[dict[str, Any]]:
        """Peak VRAM per quantization mode from span data."""
        conditions: list[str] = ["s.cuda_allocated_after_mb IS NOT NULL"]
        params: list[Any] = []

        conditions.append("r.started_at >= datetime('now', ?)")
        params.append(f"-{days} days")

        if tool:
            conditions.append("r.tool=?")
            params.append(tool)
        if gpu_name:
            conditions.append("r.gpu_name LIKE ?")
            params.append(f"%{gpu_name}%")

        where = " AND ".join(conditions)

        query = f"""\
            SELECT
                r.tool,
                r.quantization_mode,
                r.gpu_name,
                r.gpu_total_vram_mb,
                s.span_name,
                COUNT(*) as sample_count,
                ROUND(MAX(s.cuda_allocated_after_mb), 1) as peak_vram_mb,
                ROUND(AVG(s.cuda_allocated_after_mb), 1) as avg_vram_mb,
                ROUND(MIN(s.cuda_free_after_mb), 1) as min_free_mb,
                ROUND(AVG(s.duration_ms), 1) as avg_span_ms
            FROM spans s
            JOIN runs r ON s.run_id = r.id
            WHERE {where}
            GROUP BY r.tool, r.quantization_mode, r.gpu_name, r.gpu_total_vram_mb, s.span_name
            ORDER BY r.tool, peak_vram_mb DESC"""

        cur = self._conn.execute(query, params)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in rows]

    def recommend_config(
        self,
        tool: str,
        target_vram_mb: float,
        *,
        gpu_name: str | None = None,
        days: int = 90,
    ) -> list[dict[str, Any]]:
        """Find best quantization configs that fit target VRAM with minimal waste.

        Returns configs ordered by VRAM utilization closest to target
        (uses most of available VRAM without exceeding).
        """
        conditions: list[str] = ["r.success=1", "s.cuda_allocated_after_mb IS NOT NULL"]
        params: list[Any] = []

        conditions.append("r.tool=?")
        params.append(tool)

        conditions.append("r.started_at >= datetime('now', ?)")
        params.append(f"-{days} days")

        if gpu_name:
            conditions.append("r.gpu_name LIKE ?")
            params.append(f"%{gpu_name}%")

        where = " AND ".join(conditions)

        query = f"""\
            SELECT
                r.quantization_mode,
                r.gpu_name,
                r.gpu_total_vram_mb,
                s.span_name,
                COUNT(*) as sample_count,
                ROUND(MAX(s.cuda_allocated_after_mb), 1) as peak_vram_mb,
                ROUND(AVG(s.cuda_allocated_after_mb), 1) as avg_vram_mb,
                ROUND(MIN(s.cuda_free_after_mb), 1) as min_free_mb,
                ROUND(AVG(s.duration_ms), 1) as avg_span_ms,
                ROUND(MAX(s.cuda_allocated_after_mb) - ?, 1) as vram_margin_mb
            FROM spans s
            JOIN runs r ON s.run_id = r.id
            WHERE {where}
            GROUP BY r.quantization_mode, r.gpu_name, r.gpu_total_vram_mb, s.span_name
            HAVING peak_vram_mb <= ?
            ORDER BY peak_vram_mb DESC"""

        params.insert(0, target_vram_mb)
        params.append(target_vram_mb)

        cur = self._conn.execute(query, params)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in rows]

    def delete_old_runs(self, *, days: int = 90) -> int:
        """Delete runs older than N days. Returns number of deleted runs."""
        cur = self._conn.execute(
            "DELETE FROM spans WHERE run_id IN (SELECT id FROM runs WHERE started_at < datetime('now', ?))",
            (f"-{days} days",),
        )
        cur = self._conn.execute(
            "DELETE FROM runs WHERE started_at < datetime('now', ?)",
            (f"-{days} days",),
        )
        self._conn.commit()
        return cur.rowcount or 0
