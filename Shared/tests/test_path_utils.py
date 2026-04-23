"""Tests for path utilities."""

from __future__ import annotations

from pathlib import Path

from gamedev_shared.path_utils import ensure_directory, safe_filename


def test_ensure_directory_creates(tmp_path: Path):
    target = tmp_path / "a" / "b" / "c"
    result = ensure_directory(target)
    assert target.is_dir()
    assert result == target


def test_ensure_directory_idempotent(tmp_path: Path):
    ensure_directory(tmp_path / "x")
    ensure_directory(tmp_path / "x")


def test_safe_filename_basic():
    assert safe_filename("Hello World!") == "hello-world"


def test_safe_filename_truncation():
    assert len(safe_filename("a" * 100, max_len=20)) == 20


def test_safe_filename_special_chars():
    assert safe_filename("foo@bar#baz$qux") == "foobarbazqux"
