"""Tests for rocks3d.generator."""

from __future__ import annotations

import numpy as np
import pytest
import trimesh
from rocks3d.generator import generate_rock


class TestPebbleGeneration:
    def test_pebble_generates_valid_mesh(self) -> None:
        mesh = generate_rock("pebble", seed=42)
        assert isinstance(mesh, trimesh.Trimesh)
        assert len(mesh.vertices) > 0
        assert len(mesh.faces) > 0

    def test_pebble_is_watertight(self) -> None:
        mesh = generate_rock("pebble", seed=42)
        assert mesh.is_watertight

    def test_pebble_vertex_count_reasonable(self) -> None:
        mesh = generate_rock("pebble", seed=42)
        assert 40 <= len(mesh.vertices) <= 170


class TestBoulderGeneration:
    def test_boulder_generates_valid_mesh(self) -> None:
        mesh = generate_rock("boulder", seed=42)
        assert isinstance(mesh, trimesh.Trimesh)
        assert len(mesh.vertices) > 0
        assert len(mesh.faces) > 0

    def test_boulder_is_watertight(self) -> None:
        mesh = generate_rock("boulder", seed=42)
        assert mesh.is_watertight

    def test_boulder_has_more_vertices_than_pebble(self) -> None:
        pebble = generate_rock("pebble", seed=42)
        boulder = generate_rock("boulder", seed=42)
        assert len(boulder.vertices) > len(pebble.vertices)

    def test_boulder_vertex_count_reasonable(self) -> None:
        mesh = generate_rock("boulder", seed=42)
        assert 2500 <= len(mesh.vertices) <= 2600


class TestSeedReproducibility:
    def test_same_seed_same_mesh_pebble(self) -> None:
        a = generate_rock("pebble", seed=99)
        b = generate_rock("pebble", seed=99)
        assert np.allclose(a.vertices, b.vertices)

    def test_same_seed_same_mesh_boulder(self) -> None:
        a = generate_rock("boulder", seed=99)
        b = generate_rock("boulder", seed=99)
        assert np.allclose(a.vertices, b.vertices)

    def test_different_seed_different_mesh(self) -> None:
        a = generate_rock("pebble", seed=1)
        b = generate_rock("pebble", seed=2)
        assert not np.allclose(a.vertices, b.vertices)


class TestEdgeCases:
    def test_invalid_type_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unknown rock type"):
            generate_rock("mountain", seed=42)

    def test_none_seed_generates_mesh(self) -> None:
        mesh = generate_rock("pebble", seed=None)
        assert isinstance(mesh, trimesh.Trimesh)
        assert len(mesh.vertices) > 0
