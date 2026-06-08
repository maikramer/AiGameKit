"""Tests for rocks3d.erosion."""

from __future__ import annotations

import numpy as np
import trimesh
from rocks3d.erosion import apply_erosion


def _make_sphere() -> trimesh.Trimesh:
    """Create a unit icosphere for testing."""
    return trimesh.creation.icosphere(subdivisions=2, radius=1.0)


def _make_displaced_sphere(seed: int = 0) -> trimesh.Trimesh:
    """Create a displaced sphere that has height variation for erosion."""
    mesh = _make_sphere()
    rng = np.random.RandomState(seed)
    mesh.vertices += mesh.vertex_normals * rng.uniform(-0.15, 0.15, size=(len(mesh.vertices), 1))
    return mesh


class TestErosionStrength:
    def test_lower_strength_deviates_less(self) -> None:
        mesh = _make_displaced_sphere(seed=7)
        original = mesh.vertices.copy()
        modest = apply_erosion(mesh, seed=1, passes=3, strength=0.3)
        full = apply_erosion(mesh, seed=1, passes=3, strength=1.0)
        dev_modest = np.linalg.norm(modest.vertices - original)
        dev_full = np.linalg.norm(full.vertices - original)
        assert dev_modest < dev_full

    def test_zero_strength_is_identity(self) -> None:
        mesh = _make_displaced_sphere(seed=7)
        original = mesh.vertices.copy()
        out = apply_erosion(mesh, seed=1, passes=3, strength=0.0)
        assert np.allclose(original, out.vertices)


class TestErosionModifiesVertices:
    def test_erosion_modifies_vertices(self) -> None:
        mesh = _make_displaced_sphere(seed=7)
        original = mesh.vertices.copy()
        eroded = apply_erosion(mesh, seed=1, passes=3, erosion_rate=0.3)
        assert not np.allclose(original, eroded.vertices, atol=1e-10)

    def test_erosion_preserves_face_count(self) -> None:
        mesh = _make_displaced_sphere()
        eroded = apply_erosion(mesh, seed=1, passes=3)
        assert len(eroded.faces) == len(mesh.faces)

    def test_erosion_preserves_vertex_count(self) -> None:
        mesh = _make_displaced_sphere()
        eroded = apply_erosion(mesh, seed=1, passes=3)
        assert len(eroded.vertices) == len(mesh.vertices)


class TestErosionPreservesWatertight:
    def test_eroded_mesh_still_watertight(self) -> None:
        mesh = _make_displaced_sphere(seed=42)
        assert mesh.is_watertight
        eroded = apply_erosion(mesh, seed=1, passes=5)
        assert eroded.is_watertight


class TestErosionReproducible:
    def test_same_seed_same_result(self) -> None:
        mesh = _make_displaced_sphere(seed=10)
        a = apply_erosion(mesh, seed=5, passes=3, erosion_rate=0.5)
        b = apply_erosion(mesh, seed=5, passes=3, erosion_rate=0.5)
        assert np.allclose(a.vertices, b.vertices)

    def test_different_seed_different_result(self) -> None:
        mesh = _make_displaced_sphere(seed=10)
        a = apply_erosion(mesh, seed=1, passes=3)
        b = apply_erosion(mesh, seed=2, passes=3)
        assert not np.allclose(a.vertices, b.vertices)


class TestZeroPassesNoChange:
    def test_zero_passes_returns_identical_vertices(self) -> None:
        mesh = _make_displaced_sphere(seed=10)
        eroded = apply_erosion(mesh, seed=1, passes=0)
        assert np.allclose(mesh.vertices, eroded.vertices)

    def test_zero_passes_preserves_faces(self) -> None:
        mesh = _make_displaced_sphere()
        eroded = apply_erosion(mesh, seed=1, passes=0)
        assert np.array_equal(mesh.faces, eroded.faces)
