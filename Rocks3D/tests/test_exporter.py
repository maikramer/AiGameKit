"""Tests for rocks3d.exporter — GLB export with PBR materials."""

from __future__ import annotations

from pathlib import Path

import pytest
import trimesh
from rocks3d.exporter import export_glb


@pytest.fixture()
def sample_mesh() -> trimesh.Trimesh:
    return trimesh.creation.icosphere(subdivisions=2, radius=1.0)


@pytest.fixture()
def tmp_glb(tmp_path: Path) -> Path:
    return tmp_path / "rock.glb"


class TestExportGlbCreatesFile:
    def test_creates_file(self, sample_mesh: trimesh.Trimesh, tmp_glb: Path) -> None:
        result = export_glb(sample_mesh, {}, tmp_glb)
        assert result == tmp_glb
        assert tmp_glb.exists()
        assert tmp_glb.stat().st_size > 0


class TestExportGlbLoadable:
    def test_vertices_and_faces(self, sample_mesh: trimesh.Trimesh, tmp_glb: Path) -> None:
        export_glb(sample_mesh, {}, tmp_glb)
        loaded = trimesh.load(str(tmp_glb), file_type="glb")
        assert isinstance(loaded, trimesh.Scene)
        geometries = list(loaded.geometry.values())
        assert len(geometries) == 1
        mesh = geometries[0]
        assert len(mesh.vertices) > 0
        assert len(mesh.faces) > 0


class TestExportGlbOriginAtBottom:
    def test_y_min_near_zero(self, sample_mesh: trimesh.Trimesh, tmp_glb: Path) -> None:
        export_glb(sample_mesh, {}, tmp_glb)
        loaded = trimesh.load(str(tmp_glb), file_type="glb")
        mesh = next(iter(loaded.geometry.values()))
        y_min = mesh.vertices[:, 1].min()
        assert abs(y_min) < 1e-6, f"Expected y_min ≈ 0, got {y_min}"


class TestExportGlbNoTextures:
    def test_no_textures_still_works(self, sample_mesh: trimesh.Trimesh, tmp_glb: Path) -> None:
        export_glb(sample_mesh, {"albedo": None, "normal": None, "roughness": None}, tmp_glb)
        assert tmp_glb.exists()
        loaded = trimesh.load(str(tmp_glb), file_type="glb")
        mesh = next(iter(loaded.geometry.values()))
        assert len(mesh.vertices) > 0


class TestExportGlbWithAlbedo:
    def test_with_albedo_texture(self, sample_mesh: trimesh.Trimesh, tmp_glb: Path, tmp_path: Path) -> None:
        from PIL import Image

        albedo_path = tmp_path / "albedo.png"
        img = Image.new("RGB", (64, 64), color=(128, 64, 32))
        img.save(albedo_path)

        export_glb(sample_mesh, {"albedo": albedo_path}, tmp_glb)
        assert tmp_glb.exists()
        loaded = trimesh.load(str(tmp_glb), file_type="glb")
        mesh = next(iter(loaded.geometry.values()))
        assert len(mesh.vertices) > 0


class TestExportGlbCustomMaterialName:
    def test_custom_material_name(self, sample_mesh: trimesh.Trimesh, tmp_glb: Path) -> None:
        export_glb(sample_mesh, {}, tmp_glb, material_name="granite")
        assert tmp_glb.exists()
