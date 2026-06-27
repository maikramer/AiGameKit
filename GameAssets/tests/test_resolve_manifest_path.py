"""Unit tests for the gameassets manifest path resolver."""

from __future__ import annotations

from pathlib import Path

from gameassets.helpers import _resolve_manifest_path


def test_resolve_plain_name_appends_yaml(tmp_path: Path) -> None:
    (tmp_path / "manifest.yaml").write_text("assets: []", encoding="utf-8")
    resolved = _resolve_manifest_path(tmp_path / "manifest")
    assert resolved == tmp_path / "manifest.yaml"


def test_resolve_dotted_name_does_not_replace(tmp_path: Path) -> None:
    """Regression: ``Path('manifest.dark_forest').with_suffix('.yaml')`` would
    wrongly produce ``manifest.yaml``. The resolver must keep the dotted name
    and only append the extension."""
    (tmp_path / "manifest.dark_forest.yaml").write_text("assets: []", encoding="utf-8")
    (tmp_path / "manifest.yaml").write_text("assets: []", encoding="utf-8")
    resolved = _resolve_manifest_path(tmp_path / "manifest.dark_forest")
    assert resolved == tmp_path / "manifest.dark_forest.yaml"
    assert resolved != tmp_path / "manifest.yaml"


def test_resolve_explicit_yaml_returned_as_is(tmp_path: Path) -> None:
    f = tmp_path / "profile.yaml"
    f.write_text("assets: []", encoding="utf-8")
    assert _resolve_manifest_path(f) == f


def test_resolve_explicit_yml_returned_as_is(tmp_path: Path) -> None:
    f = tmp_path / "alt.yml"
    f.write_text("assets: []", encoding="utf-8")
    assert _resolve_manifest_path(f) == f


def test_resolve_prefers_yaml_over_yml(tmp_path: Path) -> None:
    (tmp_path / "name.yaml").write_text("a: 1", encoding="utf-8")
    (tmp_path / "name.yml").write_text("a: 2", encoding="utf-8")
    resolved = _resolve_manifest_path(tmp_path / "name")
    assert resolved.suffix == ".yaml"


def test_resolve_missing_file_returns_yaml_appended(tmp_path: Path) -> None:
    resolved = _resolve_manifest_path(tmp_path / "nonexistent.dark_forest")
    assert resolved == tmp_path / "nonexistent.dark_forest.yaml"


def test_resolve_with_subdirectory(tmp_path: Path) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "manifest.desert.yaml").write_text("assets: []", encoding="utf-8")
    resolved = _resolve_manifest_path(sub / "manifest.desert")
    assert resolved == sub / "manifest.desert.yaml"
