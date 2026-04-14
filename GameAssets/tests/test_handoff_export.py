"""Tests for handoff_export helpers."""

from __future__ import annotations

from pathlib import Path

from gameassets.handoff_export import _install_file


def test_install_file_noop_when_src_and_dst_identical(tmp_path: Path) -> None:
    """When output_dir is under public/, audio source and handoff dest can be the same path."""
    p = tmp_path / "clip.wav"
    p.write_bytes(b"wavdata")
    _install_file(p, p, copy=True)
    assert p.read_bytes() == b"wavdata"
