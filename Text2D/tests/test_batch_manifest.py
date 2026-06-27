"""Unit tests for the text2d batch manifest parser."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from text2d.cli import _parse_batch_manifest


def test_parse_array_of_items(tmp_path: Path) -> None:
    manifest = tmp_path / "m.json"
    manifest.write_text(
        json.dumps(
            [
                {"id": "a", "prompt": "alpha", "output": "a.png"},
                {"id": "b", "prompt": "beta", "output": "b.png"},
            ]
        ),
        encoding="utf-8",
    )
    items = _parse_batch_manifest(manifest)
    assert len(items) == 2
    assert items[0]["id"] == "a"
    assert items[1]["output"] == "b.png"


def test_parse_single_object_wraps_into_array(tmp_path: Path) -> None:
    manifest = tmp_path / "single.json"
    manifest.write_text(
        json.dumps({"id": "solo", "prompt": "p", "output": "o.png"}),
        encoding="utf-8",
    )
    items = _parse_batch_manifest(manifest)
    assert len(items) == 1
    assert items[0]["id"] == "solo"


def test_parse_rejects_non_object_non_array(tmp_path: Path) -> None:
    manifest = tmp_path / "num.json"
    manifest.write_text("42", encoding="utf-8")
    with pytest.raises(ValueError, match="array JSON ou objeto"):
        _parse_batch_manifest(manifest)


def test_parse_rejects_string_top_level(tmp_path: Path) -> None:
    manifest = tmp_path / "str.json"
    manifest.write_text('"hello"', encoding="utf-8")
    with pytest.raises(ValueError, match="array JSON ou objeto"):
        _parse_batch_manifest(manifest)


def test_parse_missing_required_key_raises(tmp_path: Path) -> None:
    manifest = tmp_path / "missing.json"
    manifest.write_text(
        json.dumps([{"id": "ok", "prompt": "p", "output": "o.png"}, {"id": "bad"}]),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Item 1 falta: prompt, output"):
        _parse_batch_manifest(manifest)


def test_parse_missing_keys_in_single_object(tmp_path: Path) -> None:
    manifest = tmp_path / "missing_single.json"
    manifest.write_text(json.dumps({"id": "x"}), encoding="utf-8")
    with pytest.raises(ValueError, match="Item 0 falta: prompt, output"):
        _parse_batch_manifest(manifest)


def test_parse_invalid_json_raises(tmp_path: Path) -> None:
    manifest = tmp_path / "broken.json"
    manifest.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        _parse_batch_manifest(manifest)


def test_parse_empty_array_ok(tmp_path: Path) -> None:
    manifest = tmp_path / "empty.json"
    manifest.write_text("[]", encoding="utf-8")
    assert _parse_batch_manifest(manifest) == []
