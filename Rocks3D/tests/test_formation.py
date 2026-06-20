"""Tests for multi-chunk rock formations."""

from __future__ import annotations

import numpy as np
import pytest

from rocks3d.formation import STYLES, generate_formation


@pytest.mark.parametrize("style", STYLES)
def test_formation_generates_valid_mesh(style: str) -> None:
    mesh = generate_formation(style, seed=1, quality="fast")
    assert len(mesh.vertices) > 0
    assert len(mesh.faces) > 0
    # Non-degenerate volume in every axis.
    dims = mesh.bounds[1] - mesh.bounds[0]
    assert np.all(dims > 0.1)


@pytest.mark.parametrize("style", STYLES)
def test_formation_sits_on_ground(style: str) -> None:
    mesh = generate_formation(style, seed=2, quality="fast")
    # Base dropped to y = 0 and recentred on XZ.
    assert mesh.bounds[0][1] == pytest.approx(0.0, abs=1e-6)
    centre_xz = (mesh.bounds[0] + mesh.bounds[1]) * 0.5
    assert centre_xz[0] == pytest.approx(0.0, abs=1e-6)
    assert centre_xz[2] == pytest.approx(0.0, abs=1e-6)


def test_formation_is_reproducible() -> None:
    a = generate_formation("outcrop", seed=42, quality="fast")
    b = generate_formation("outcrop", seed=42, quality="fast")
    assert a.vertices.shape == b.vertices.shape
    assert np.allclose(a.vertices, b.vertices)


def test_unknown_style_raises() -> None:
    with pytest.raises(ValueError, match="Unknown formation style"):
        generate_formation("not-a-style", seed=0)


def test_chunk_count_override_changes_geometry() -> None:
    small = generate_formation("spire-cluster", seed=5, quality="fast", chunks=2)
    big = generate_formation("spire-cluster", seed=5, quality="fast", chunks=7)
    # More chunks → a wider footprint (different mesh).
    assert len(big.vertices) != len(small.vertices)
