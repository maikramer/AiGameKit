"""Tests for X-Part MC retry helpers."""

from __future__ import annotations

from part3d.pipeline import _mc_level_candidates


def test_mc_level_candidates_include_primary_and_zero() -> None:
    levels = _mc_level_candidates(-1.0 / 512.0)
    assert levels[0] == -1.0 / 512.0
    assert 0.0 in levels
    assert len(levels) >= 3
