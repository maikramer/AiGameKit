"""Tests for gamedev_shared.progress — unified JSONL progress protocol."""

from __future__ import annotations

import json

from gamedev_shared.progress import (
    STATUS_ERROR,
    STATUS_OK,
    STATUS_PROGRESS,
    STATUS_SKIPPED,
    TOOL_TEXT3D,
    emit_progress,
    emit_result,
    parse_progress_line,
)


class TestParseProgressLine:
    def test_valid_progress_line(self) -> None:
        raw = json.dumps({"id": "hero", "tool": "text3d", "status": "progress", "phase": "inference", "percent": 45.0})
        result = parse_progress_line(raw)
        assert result is not None
        assert result["id"] == "hero"
        assert result["status"] == "progress"
        assert result["percent"] == 45.0

    def test_valid_result_line(self) -> None:
        raw = json.dumps({"id": "hero", "tool": "text3d", "status": "ok", "output": "hero.glb", "seconds": 12.3})
        result = parse_progress_line(raw)
        assert result is not None
        assert result["status"] == "ok"
        assert result["output"] == "hero.glb"

    def test_empty_line(self) -> None:
        assert parse_progress_line("") is None
        assert parse_progress_line("   ") is None

    def test_invalid_json(self) -> None:
        assert parse_progress_line("not json") is None
        assert parse_progress_line("{broken") is None

    def test_missing_id(self) -> None:
        raw = json.dumps({"status": "ok"})
        assert parse_progress_line(raw) is None

    def test_missing_status(self) -> None:
        raw = json.dumps({"id": "hero"})
        assert parse_progress_line(raw) is None

    def test_non_dict(self) -> None:
        raw = json.dumps([1, 2, 3])
        assert parse_progress_line(raw) is None

    def test_line_with_whitespace(self) -> None:
        raw = "  " + json.dumps({"id": "x", "status": "ok"}) + "  \n"
        assert parse_progress_line(raw) is not None

    def test_backward_compat_no_tool_field(self) -> None:
        raw = json.dumps({"id": "hero", "status": "ok", "output": "hero.glb", "seconds": 5.0})
        result = parse_progress_line(raw)
        assert result is not None
        assert result["tool"] == "unknown"


class TestEmitProgress:
    def test_emits_valid_jsonl(self, capsys: object) -> None:
        emit_progress("hero", TOOL_TEXT3D, phase="inference", percent=50.0)
        captured = capsys.readouterr()
        data = json.loads(captured.out.strip())
        assert data["id"] == "hero"
        assert data["tool"] == "text3d"
        assert data["status"] == STATUS_PROGRESS
        assert data["phase"] == "inference"
        assert data["percent"] == 50.0

    def test_minimal_fields(self, capsys: object) -> None:
        emit_progress("item1", "text2d")
        captured = capsys.readouterr()
        data = json.loads(captured.out.strip())
        assert data["id"] == "item1"
        assert data["tool"] == "text2d"
        assert data["status"] == STATUS_PROGRESS
        assert "phase" not in data
        assert "percent" not in data

    def test_meta_fields(self, capsys: object) -> None:
        emit_progress("hero", "text3d", phase="inference", percent=30, vram_mb=4200)
        captured = capsys.readouterr()
        data = json.loads(captured.out.strip())
        assert data["meta"]["vram_mb"] == 4200

    def test_percent_rounded(self, capsys: object) -> None:
        emit_progress("hero", "text3d", percent=33.33333)
        captured = capsys.readouterr()
        data = json.loads(captured.out.strip())
        assert data["percent"] == 33.3


class TestEmitResult:
    def test_ok_result(self, capsys: object) -> None:
        emit_result("hero", TOOL_TEXT3D, STATUS_OK, output="hero.glb", seconds=12.3, faces=8000)
        captured = capsys.readouterr()
        data = json.loads(captured.out.strip())
        assert data["status"] == STATUS_OK
        assert data["output"] == "hero.glb"
        assert data["seconds"] == 12.3
        assert data["faces"] == 8000

    def test_error_result(self, capsys: object) -> None:
        emit_result("hero", TOOL_TEXT3D, STATUS_ERROR, error="OOM")
        captured = capsys.readouterr()
        data = json.loads(captured.out.strip())
        assert data["status"] == STATUS_ERROR
        assert data["error"] == "OOM"

    def test_skipped_result(self, capsys: object) -> None:
        emit_result("hero", TOOL_TEXT3D, STATUS_SKIPPED, output="hero.glb")
        captured = capsys.readouterr()
        data = json.loads(captured.out.strip())
        assert data["status"] == STATUS_SKIPPED

    def test_seconds_rounded(self, capsys: object) -> None:
        emit_result("x", "text3d", STATUS_OK, seconds=12.3456)
        captured = capsys.readouterr()
        data = json.loads(captured.out.strip())
        assert data["seconds"] == 12.35

    def test_meta_in_result(self, capsys: object) -> None:
        emit_result("hero", "text3d", STATUS_OK, custom="value")
        captured = capsys.readouterr()
        data = json.loads(captured.out.strip())
        assert data["meta"]["custom"] == "value"
