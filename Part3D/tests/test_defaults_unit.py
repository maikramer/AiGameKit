"""Constantes em ``defaults`` (sem pipeline)."""

from __future__ import annotations

import part3d.defaults as d


def test_default_inference_positive() -> None:
    assert d.DEFAULT_NUM_INFERENCE_STEPS > 0


def test_default_octree_resolution() -> None:
    assert d.DEFAULT_OCTREE_RESOLUTION >= 64
