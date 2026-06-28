"""Testes unitários para gameassets.validator (validate_row em árvore de saída fake)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gameassets.manifest import ManifestRow
from gameassets.profile import GameProfile
from gameassets.validator import ValidationResult, validate_row

_BPY_AVAILABLE = importlib.util.find_spec("bpy") is not None


def _profile(output_dir: str = ".", path_layout: str = "split") -> GameProfile:
    return GameProfile.from_dict(
        {
            "title": "T",
            "genre": "G",
            "tone": "t",
            "style_preset": "lowpoly",
            "output_dir": output_dir,
            "path_layout": path_layout,
        }
    )


def _row(
    rid: str = "hero",
    *,
    generate_3d: bool = True,
    generate_audio: bool = False,
    generate_lod: bool = False,
    generate_collision: bool = False,
) -> ManifestRow:
    return ManifestRow(
        id=rid,
        idea="an asset",
        kind="prop",
        generate_3d=generate_3d,
        generate_audio=generate_audio,
        generate_lod=generate_lod,
        generate_collision=generate_collision,
    )


def _write(path: Path, size_bytes: int = 64) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size_bytes)


def _mock_textured_mesh(face_count: int = 1000) -> list[SimpleNamespace]:
    tex_node = SimpleNamespace(type="TEX_IMAGE", image=object())
    material = SimpleNamespace(node_tree=SimpleNamespace(nodes=[tex_node]))
    slot = SimpleNamespace(material=material)
    return [SimpleNamespace(material_slots=[slot])]


class TestValidationResult:
    def test_ok_when_no_errors(self) -> None:
        assert ValidationResult(row_id="x").ok is True

    def test_not_ok_with_errors(self) -> None:
        r = ValidationResult(row_id="x")
        r.errors.append("boom")
        assert r.ok is False


class TestMissingAssets:
    def test_missing_glb_is_error(self, tmp_path: Path) -> None:
        result = validate_row(_row("hero", generate_3d=True), _profile(), tmp_path)
        assert result.ok is False
        assert any("GLB" in e for e in result.errors)

    def test_missing_audio_is_error(self, tmp_path: Path) -> None:
        result = validate_row(_row("hero", generate_3d=False, generate_audio=True), _profile(), tmp_path)
        assert result.ok is False
        assert any("Áudio" in e for e in result.errors)


class TestValidTree:
    def test_present_glb_passes(self, tmp_path: Path) -> None:
        _write(tmp_path / "meshes" / "hero.glb")
        result = validate_row(_row("hero", generate_3d=True), _profile(), tmp_path)
        assert result.errors == []
        assert result.ok is True

    def test_present_audio_passes(self, tmp_path: Path) -> None:
        _write(tmp_path / "audio" / "hero.wav")
        result = validate_row(_row("hero", generate_3d=False, generate_audio=True), _profile(), tmp_path)
        assert result.errors == []
        assert result.ok is True

    def test_no_3d_no_audio_passes(self, tmp_path: Path) -> None:
        result = validate_row(_row("hero", generate_3d=False, generate_audio=False), _profile(), tmp_path)
        assert result.ok is True
        assert result.errors == []


class TestFileSize:
    def test_oversized_glb_is_error(self, tmp_path: Path) -> None:
        glb = tmp_path / "meshes" / "hero.glb"
        _write(glb, size_bytes=1024 * 1024)
        result = validate_row(_row("hero", generate_3d=True), _profile(), tmp_path, max_file_size_mb=0.001)
        assert result.ok is False
        assert any("demasiado grande" in e for e in result.errors)

    def test_near_limit_emits_warning_only(self, tmp_path: Path) -> None:
        glb = tmp_path / "meshes" / "hero.glb"
        _write(glb, size_bytes=1024 * 1024)
        # 1 MB vs limite 10 MB: > 80% de 10 MB? não -> sem warning de tamanho.
        # Usar limite 1.1 MB para que 1 MB > 0.8*1.1=0.88 MB -> warning, mas < 1.1 -> sem erro.
        result = validate_row(_row("hero", generate_3d=True), _profile(), tmp_path, max_file_size_mb=1.1)
        assert result.ok is True
        assert any("grande" in w for w in result.warnings)


class TestLodAndCollision:
    def test_missing_lods_are_errors(self, tmp_path: Path) -> None:
        _write(tmp_path / "meshes" / "hero.glb")
        result = validate_row(_row("hero", generate_3d=True, generate_lod=True), _profile(), tmp_path)
        lod_errors = [e for e in result.errors if "LOD" in e]
        assert len(lod_errors) == 3

    def test_present_lods_no_lod_errors(self, tmp_path: Path) -> None:
        meshes = tmp_path / "meshes"
        _write(meshes / "hero.glb")
        for level in range(3):
            _write(meshes / f"hero_lod{level}.glb")
        result = validate_row(_row("hero", generate_3d=True, generate_lod=True), _profile(), tmp_path)
        assert not [e for e in result.errors if "LOD" in e]
        assert result.ok is True

    def test_missing_collision_is_error(self, tmp_path: Path) -> None:
        _write(tmp_path / "meshes" / "hero.glb")
        result = validate_row(_row("hero", generate_3d=True, generate_collision=True), _profile(), tmp_path)
        assert any("Collision" in e for e in result.errors)
        assert result.ok is False

    def test_present_collision_no_error(self, tmp_path: Path) -> None:
        meshes = tmp_path / "meshes"
        _write(meshes / "hero.glb")
        _write(meshes / "hero_collision.glb")
        result = validate_row(_row("hero", generate_3d=True, generate_collision=True), _profile(), tmp_path)
        assert not [e for e in result.errors if "Collision" in e]
        assert result.ok is True


class TestFlatLayout:
    def test_flat_layout_resolves_subdir(self, tmp_path: Path) -> None:
        glb = tmp_path / "Props" / "crate_01.glb"
        _write(glb)
        row = _row("Props/crate_01", generate_3d=True)
        result = validate_row(row, _profile(path_layout="flat"), tmp_path)
        assert result.ok is True
        assert result.errors == []

    def test_flat_layout_missing_is_error(self, tmp_path: Path) -> None:
        row = _row("Props/crate_01", generate_3d=True)
        result = validate_row(row, _profile(path_layout="flat"), tmp_path)
        assert result.ok is False
        assert any("crate_01" in e for e in result.errors)


@pytest.mark.skipif(not _BPY_AVAILABLE, reason="geometria controlada requer bpy_mesh (bpy)")
class TestGeometryChecks:
    """Poly-count e textura — requer patch de gamedev_shared.bpy_mesh (presente com bpy)."""

    def test_high_poly_count_is_error(self, tmp_path: Path) -> None:
        _write(tmp_path / "meshes" / "hero.glb")
        with (
            patch("gamedev_shared.bpy_mesh.load_glb", return_value=_mock_textured_mesh(200000)),
            patch("gamedev_shared.bpy_mesh.face_count", side_effect=lambda _o: 200000),
        ):
            result = validate_row(_row("hero", generate_3d=True), _profile(), tmp_path, max_poly_count=100_000)
        assert result.ok is False
        assert any("Poly count elevado" in e for e in result.errors)

    def test_near_limit_poly_is_warning(self, tmp_path: Path) -> None:
        _write(tmp_path / "meshes" / "hero.glb")
        with (
            patch("gamedev_shared.bpy_mesh.load_glb", return_value=_mock_textured_mesh(90000)),
            patch("gamedev_shared.bpy_mesh.face_count", side_effect=lambda _o: 90000),
        ):
            result = validate_row(_row("hero", generate_3d=True), _profile(), tmp_path, max_poly_count=100_000)
        assert result.ok is True
        assert any("Poly count alto" in w for w in result.warnings)

    def test_missing_texture_is_warning(self, tmp_path: Path) -> None:
        _write(tmp_path / "meshes" / "hero.glb")
        empty_obj = [SimpleNamespace(material_slots=[])]
        with (
            patch("gamedev_shared.bpy_mesh.load_glb", return_value=empty_obj),
            patch("gamedev_shared.bpy_mesh.face_count", side_effect=lambda _o: 1000),
        ):
            result = validate_row(_row("hero", generate_3d=True), _profile(), tmp_path)
        assert result.ok is True
        assert any("textura" in w for w in result.warnings)

    def test_empty_geometry_is_error(self, tmp_path: Path) -> None:
        _write(tmp_path / "meshes" / "hero.glb")
        with patch("gamedev_shared.bpy_mesh.load_glb", return_value=[]):
            result = validate_row(_row("hero", generate_3d=True), _profile(), tmp_path)
        assert result.ok is False
        assert any("geometria" in e for e in result.errors)
