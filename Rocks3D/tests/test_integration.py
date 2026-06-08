"""End-to-end integration tests for the full rocks3d pipeline.

Tests exercise the complete generate → UV → texture → export → reload
pipeline for both rock types, verifying mesh properties, seed reproducibility,
and error handling without requiring GPU or external tools.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import trimesh
from rocks3d.defaults import get_preset
from rocks3d.erosion import apply_erosion
from rocks3d.exporter import export_glb
from rocks3d.generator import generate_rock
from rocks3d.texture import generate_pbr_textures
from rocks3d.uv_mapping import apply_uv_spherical, apply_uv_xatlas


def _load_glb_mesh(path: Path) -> trimesh.Trimesh:
    """Load a GLB file and return the first Trimesh geometry.

    trimesh.load() returns a Scene when the GLB contains a single mesh;
    this helper extracts the geometry in both Scene and Trimesh cases.

    Args:
        path: Path to the GLB file.

    Returns:
        The loaded :class:`trimesh.Trimesh`.
    """
    loaded = trimesh.load(str(path))
    if isinstance(loaded, trimesh.Scene):
        geometries = list(loaded.geometry.values())
        assert len(geometries) == 1, f"Expected 1 geometry, got {len(geometries)}"
        return geometries[0]
    return loaded


class TestEndToEndPipeline:
    """Test the full generate → erosion → UV → texture → export → load pipeline."""

    def test_boulder_full_pipeline_produces_glb(self, tmp_path: Path) -> None:
        """Generate boulder through full pipeline, verify GLB is valid."""
        mesh = generate_rock("boulder", seed=42)
        mesh = apply_erosion(mesh, seed=42, passes=2)
        mesh = apply_uv_xatlas(mesh)
        preset = get_preset("boulder", "medium")
        textures = generate_pbr_textures(mesh, preset, seed=42, output_dir=tmp_path / "textures")
        out = export_glb(mesh, textures, tmp_path / "boulder.glb")

        assert out.exists()
        assert out.stat().st_size > 0

        geom = _load_glb_mesh(out)
        assert len(geom.vertices) > 0
        assert len(geom.faces) > 0
        # exporter translates mesh so y_min == 0
        assert geom.vertices[:, 1].min() < 0.01

    def test_pebble_full_pipeline_produces_glb(self, tmp_path: Path) -> None:
        """Generate pebble through full pipeline with spherical UV, verify GLB."""
        mesh = generate_rock("pebble", seed=42)
        mesh = apply_uv_spherical(mesh)
        preset = get_preset("pebble", "medium")
        textures = generate_pbr_textures(mesh, preset, seed=42, output_dir=tmp_path / "textures")
        out = export_glb(mesh, textures, tmp_path / "pebble.glb")

        assert out.exists()
        assert out.stat().st_size > 0

        geom = _load_glb_mesh(out)
        assert len(geom.vertices) > 0
        assert len(geom.faces) > 0
        assert geom.vertices[:, 1].min() < 0.01


class TestSeedReproducibilityEndToEnd:
    """Verify that seeded full-pipeline runs produce identical output."""

    def test_boulder_seed_reproducibility_via_export(self, tmp_path: Path) -> None:
        """Two full pipeline runs with same seed produce identical GLB vertices."""
        results = []
        for run_idx in range(2):
            mesh = generate_rock("boulder", seed=42)
            mesh = apply_erosion(mesh, seed=42, passes=2)
            mesh = apply_uv_spherical(mesh)
            out = export_glb(mesh, {}, tmp_path / f"boulder_{run_idx}.glb")
            geom = _load_glb_mesh(out)
            results.append(geom.vertices.copy())

        assert np.allclose(results[0], results[1])

    def test_different_seeds_different_meshes(self) -> None:
        """Different seeds produce different meshes in the full pipeline."""
        meshes = []
        for seed in (1, 2):
            mesh = generate_rock("boulder", seed=seed)
            mesh = apply_erosion(mesh, seed=seed, passes=2)
            meshes.append(mesh.vertices.copy())

        assert not np.allclose(meshes[0], meshes[1])


class TestRockTypes:
    """Verify behaviour across rock types and invalid inputs."""

    def test_both_types_generate_valid_glb(self, tmp_path: Path) -> None:
        """Both pebble and boulder generate valid GLB through full pipeline."""
        for rock_type in ("pebble", "boulder"):
            mesh = generate_rock(rock_type, seed=42)
            mesh = apply_uv_spherical(mesh)
            preset = get_preset(rock_type, "medium")
            textures = generate_pbr_textures(mesh, preset, seed=42, output_dir=tmp_path / f"textures_{rock_type}")
            out = export_glb(mesh, textures, tmp_path / f"{rock_type}.glb")

            assert out.exists(), f"{rock_type} GLB not created"
            geom = _load_glb_mesh(out)
            assert len(geom.vertices) > 0, f"{rock_type} has no vertices"
            assert len(geom.faces) > 0, f"{rock_type} has no faces"

    def test_invalid_type_raises_error(self) -> None:
        """Invalid rock type raises ValueError from generate_rock."""
        with pytest.raises(ValueError, match="Unknown rock type"):
            generate_rock("mountain", seed=42)


class TestMeshProperties:
    """Verify geometric properties of exported meshes."""

    def test_boulder_more_vertices_than_pebble(self) -> None:
        """Boulder always has more vertices than pebble at same quality."""
        pebble = generate_rock("pebble", seed=42)
        boulder = generate_rock("boulder", seed=42)
        assert len(boulder.vertices) > len(pebble.vertices)

    def test_meshes_are_watertight(self) -> None:
        """Both rock types produce watertight meshes after generation."""
        for rock_type in ("pebble", "boulder"):
            mesh = generate_rock(rock_type, seed=42)
            assert mesh.is_watertight, f"{rock_type} mesh is not watertight"

    def test_export_preserves_face_count(self, tmp_path: Path) -> None:
        """Export → reload preserves the number of faces."""
        mesh = generate_rock("boulder", seed=42)
        mesh = apply_uv_spherical(mesh)
        original_faces = len(mesh.faces)
        out = export_glb(mesh, {}, tmp_path / "faces_test.glb")
        geom = _load_glb_mesh(out)
        assert len(geom.faces) == original_faces

    def test_export_originates_at_ground(self, tmp_path: Path) -> None:
        """Exported mesh has its lowest vertex at y ≈ 0."""
        mesh = generate_rock("boulder", seed=42)
        out = export_glb(mesh, {}, tmp_path / "origin_test.glb")
        geom = _load_glb_mesh(out)
        assert geom.vertices[:, 1].min() >= -0.01
        assert geom.vertices[:, 1].min() < 0.01
