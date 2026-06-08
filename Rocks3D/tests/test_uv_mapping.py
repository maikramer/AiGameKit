"""Tests for rocks3d.uv_mapping."""

from __future__ import annotations

import numpy as np
import trimesh
from rocks3d.uv_mapping import apply_uv_spherical, apply_uv_xatlas


def _make_icosphere() -> trimesh.Trimesh:
    """Create a small icosphere for testing."""
    return trimesh.creation.icosphere(subdivisions=2, radius=1.0)


class TestSphericalUV:
    """Tests for spherical UV projection."""

    def test_spherical_uv_produces_valid_coords(self) -> None:
        """Spherical projection should produce UVs in [0, 1]."""
        mesh = _make_icosphere()
        result = apply_uv_spherical(mesh)
        uv = result.visual.uv
        assert uv is not None
        assert uv.shape[0] > 0
        assert uv.shape[1] == 2
        assert np.all(uv >= 0.0)
        assert np.all(uv <= 1.0)

    def test_uv_shape_matches_vertices(self) -> None:
        """UV array should have one entry per vertex."""
        mesh = _make_icosphere()
        n_verts = len(mesh.vertices)
        result = apply_uv_spherical(mesh)
        uv = result.visual.uv
        assert uv.shape == (n_verts, 2)


class TestXatlasUV:
    """Tests for xatlas UV (or fallback)."""

    def test_xatlas_uv_or_fallback_works(self) -> None:
        """apply_uv_xatlas should not crash and should return a mesh with UV."""
        mesh = _make_icosphere()
        result = apply_uv_xatlas(mesh)
        uv = result.visual.uv
        assert uv is not None
        assert uv.shape[0] > 0
        assert uv.shape[1] == 2
        assert np.all(uv >= 0.0)
        assert np.all(uv <= 1.0)

    def test_xatlas_preserves_faces(self) -> None:
        """Resulting mesh should still have faces (not degenerate)."""
        mesh = _make_icosphere()
        result = apply_uv_xatlas(mesh)
        assert len(result.faces) > 0
        assert result.faces.shape[1] == 3
