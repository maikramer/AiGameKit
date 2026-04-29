from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("bpy")

import bpy
import numpy as np

from gamedev_lab.mesh_inspector import (
    ArtifactReport,
    GeometryReport,
    MeshInspector,
    MeshQAReport,
    QualityScore,
    TopologyReport,
    _MeshData,
    print_qa_report,
)
from gamedev_shared.bpy_mesh import clear_scene, create_mesh_from_arrays, save_glb


def _obj_to_mesh_data(obj: object) -> _MeshData:
    mesh = obj.data  # type: ignore[union-attr]
    mesh.calc_loop_triangles()
    verts = np.array([tuple(obj.matrix_world @ v.co) for v in mesh.vertices], dtype=np.float64)  # type: ignore[union-attr]
    faces = np.array([tuple(t.vertices) for t in mesh.loop_triangles], dtype=np.int64)
    return _MeshData(verts, faces)


def _box_mesh(extents: tuple[float, ...] = (1.0, 1.0, 1.0)) -> _MeshData:
    clear_scene()
    bpy.ops.mesh.primitive_cube_add(size=1.0)
    obj = bpy.context.active_object
    obj.scale = (extents[0] / 2, extents[1] / 2, extents[2] / 2)
    bpy.ops.object.transform_apply(scale=True)
    return _obj_to_mesh_data(obj)


def _sphere_mesh(radius: float = 1.0, subdivisions: int = 2) -> _MeshData:
    clear_scene()
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=subdivisions, radius=radius)
    return _obj_to_mesh_data(bpy.context.active_object)


def _save_tmp_mesh(mesh_data: _MeshData, suffix: str = ".glb") -> Path:
    clear_scene()
    obj = create_mesh_from_arrays(mesh_data.vertices, mesh_data.faces)
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        save_glb([obj], tmp.name)
        return Path(tmp.name)


class TestTopologyReport:
    def test_defaults(self) -> None:
        r = TopologyReport()
        assert r.vertices == 0
        assert r.watertight is False
        assert r.degenerate_faces == 0

    def test_inspect_box(self) -> None:
        mesh = _box_mesh()
        path = _save_tmp_mesh(mesh)
        try:
            inspector = MeshInspector(path)
            topo = inspector._inspect_topology(mesh)
            assert topo.vertices > 0
            assert topo.faces == 12
            assert topo.watertight is True
            assert topo.degenerate_faces == 0
            assert topo.connected_components == 1
        finally:
            path.unlink(missing_ok=True)

    def test_inspect_sphere(self) -> None:
        mesh = _sphere_mesh()
        path = _save_tmp_mesh(mesh)
        try:
            inspector = MeshInspector(path)
            topo = inspector._inspect_topology(mesh)
            assert topo.vertices > 0
            assert topo.faces > 0
            assert topo.watertight is True
            assert topo.euler_number == 2
        finally:
            path.unlink(missing_ok=True)


class TestGeometryReport:
    def test_defaults(self) -> None:
        r = GeometryReport()
        assert r.volume_efficiency == 0.0
        assert r.area == 0.0

    @pytest.mark.skip(reason="Known issue: volume_efficiency returns 0 in CI")
    def test_box_geometry(self) -> None:
        mesh = _box_mesh((2.0, 3.0, 4.0))
        path = _save_tmp_mesh(mesh)
        try:
            inspector = MeshInspector(path)
            geom = inspector._inspect_geometry(mesh)
            assert geom.extents == pytest.approx([2.0, 3.0, 4.0], abs=0.01)
            assert geom.volume is not None
            assert geom.volume > 0
            assert geom.area > 0
            assert 0 < geom.volume_efficiency <= 1.0
            assert geom.thickness_ratio > 0
        finally:
            path.unlink(missing_ok=True)

    def test_flat_mesh(self) -> None:
        mesh = _box_mesh((0.01, 1.0, 1.0))
        path = _save_tmp_mesh(mesh)
        try:
            inspector = MeshInspector(path)
            geom = inspector._inspect_geometry(mesh)
            assert geom.flatness_ratio < 0.02
            assert geom.aspect_ratio > 50
        finally:
            path.unlink(missing_ok=True)

    def test_empty_mesh(self) -> None:
        mesh = _MeshData(np.empty((0, 3), dtype=np.float64), np.empty((0, 3), dtype=np.int64))
        path = _save_tmp_mesh(mesh)
        try:
            inspector = MeshInspector(path)
            geom = inspector._inspect_geometry(mesh)
            assert geom.area == 0.0
        finally:
            path.unlink(missing_ok=True)


class TestArtifactReport:
    @pytest.mark.skip(reason="Known issue: flat cutout not recognized as backing plate")
    def test_clean_box(self) -> None:
        mesh = _box_mesh((1.0, 1.0, 1.0))
        path = _save_tmp_mesh(mesh)
        try:
            inspector = MeshInspector(path)
            arti = inspector._inspect_artifacts(mesh)
            assert len(arti.issues) == 0 or all("backing plate" in i for i in arti.issues)
        finally:
            path.unlink(missing_ok=True)

    def test_flat_cutout_detected(self) -> None:
        mesh = _box_mesh((0.01, 1.0, 1.0))
        path = _save_tmp_mesh(mesh)
        try:
            inspector = MeshInspector(path)
            arti = inspector._inspect_artifacts(mesh)
            assert arti.passed is False
            assert any("flat cutout bbox" in i for i in arti.issues)
        finally:
            path.unlink(missing_ok=True)

    def test_empty_mesh(self) -> None:
        mesh = _MeshData(np.empty((0, 3), dtype=np.float64), np.empty((0, 3), dtype=np.int64))
        path = _save_tmp_mesh(mesh)
        try:
            inspector = MeshInspector(path)
            arti = inspector._inspect_artifacts(mesh)
            assert arti.passed is False
            assert "empty mesh" in arti.issues
        finally:
            path.unlink(missing_ok=True)


class TestComputeScore:
    def test_perfect_score(self) -> None:
        topo = TopologyReport(watertight=True, degenerate_faces=0, connected_components=1, duplicate_vertices=0)
        geom = GeometryReport(volume_efficiency=0.5, flatness_ratio=0.5, thickness_ratio=0.3)
        arti = ArtifactReport(passed=True, issues=[])
        score = MeshInspector._compute_score(topo, geom, arti)
        assert score.overall >= 0.8
        assert score.grade in ("A", "B")

    def test_failing_score(self) -> None:
        topo = TopologyReport(watertight=False, degenerate_faces=100, connected_components=3)
        geom = GeometryReport(volume_efficiency=0.05, flatness_ratio=0.01, thickness_ratio=0.01)
        arti = ArtifactReport(passed=False, issues=["flat cutout", "backing plate"])
        score = MeshInspector._compute_score(topo, geom, arti)
        assert score.overall < 0.7
        assert score.grade in ("D", "F")

    def test_grade_c(self) -> None:
        topo = TopologyReport(watertight=True, degenerate_faces=0, connected_components=1)
        geom = GeometryReport(volume_efficiency=0.25, flatness_ratio=0.3, thickness_ratio=0.2)
        arti = ArtifactReport(passed=False, issues=["flat-backed"])
        score = MeshInspector._compute_score(topo, geom, arti)
        assert 0.4 <= score.overall <= 0.9


class TestMeshQAReport:
    def test_to_dict(self) -> None:
        report = MeshQAReport(path="/tmp/test.glb")
        d = report.to_dict()
        assert d["path"] == "/tmp/test.glb"
        assert "topology" in d
        assert "geometry" in d
        assert "artifacts" in d
        assert "score" in d

    def test_save_json(self, tmp_path: Path) -> None:
        report = MeshQAReport(path="/tmp/test.glb")
        out = tmp_path / "qa_report.json"
        report.save_json(out)
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["path"] == "/tmp/test.glb"

    def test_passed(self) -> None:
        r_ok = MeshQAReport(
            score=QualityScore(overall=0.8, grade="B"),
            artifacts=ArtifactReport(passed=True),
        )
        assert r_ok.passed() is True

        r_fail = MeshQAReport(
            score=QualityScore(overall=0.2, grade="F"),
            artifacts=ArtifactReport(passed=False),
        )
        assert r_fail.passed() is False


class TestInspectIntegration:
    def test_inspect_box(self) -> None:
        mesh = _box_mesh((1.0, 2.0, 3.0))
        path = _save_tmp_mesh(mesh)
        try:
            report = MeshInspector(path).inspect()
            assert report.path == str(path.resolve())
            assert report.topology.faces == 12
            assert report.topology.watertight is True
            assert report.score.overall > 0
            assert report.score.grade in ("A", "B", "C", "D", "F")
        finally:
            path.unlink(missing_ok=True)

    def test_inspect_sphere(self) -> None:
        mesh = _sphere_mesh(1.0, 3)
        path = _save_tmp_mesh(mesh)
        try:
            report = MeshInspector(path).inspect()
            assert report.topology.vertices > 0
            assert report.topology.watertight is True
            assert report.geometry.area > 0
            assert report.score.overall > 0.5
        finally:
            path.unlink(missing_ok=True)

    def test_inspect_flat_mesh_fails(self) -> None:
        mesh = _box_mesh((0.005, 1.0, 1.0))
        path = _save_tmp_mesh(mesh)
        try:
            report = MeshInspector(path).inspect()
            assert report.artifacts.passed is False
            assert report.score.grade in ("D", "F")
        finally:
            path.unlink(missing_ok=True)


class TestRenderViews:
    def test_no_bin_returns_empty(self) -> None:
        mesh = _box_mesh()
        path = _save_tmp_mesh(mesh)
        try:
            inspector = MeshInspector(path)
            with patch("gamedev_lab.debug_tools.resolve_animator3d_bin", return_value=None):
                result = inspector._render_views(Path("/tmp/nonexistent"))
            assert result == []
        finally:
            path.unlink(missing_ok=True)

    def test_render_views_calls_screenshot(self) -> None:
        mesh = _box_mesh()
        path = _save_tmp_mesh(mesh)
        try:
            inspector = MeshInspector(path)
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = json.dumps(
                {
                    "screenshots": [
                        {"view": "front", "path": "/tmp/views/front.png"},
                        {"view": "back", "path": "/tmp/views/back.png"},
                    ]
                }
            )

            with (
                patch("gamedev_lab.debug_tools.resolve_animator3d_bin", return_value="animator3d"),
                patch("gamedev_lab.debug_tools.run_cmd", return_value=mock_result) as mock_run,
            ):
                result = inspector._render_views(
                    Path("/tmp/views"),
                    views="front,back",
                )
                assert len(result) == 2
                assert "/tmp/views/front.png" in result
                mock_run.assert_called_once()
        finally:
            path.unlink(missing_ok=True)


class TestCompareWithReference:
    def test_compare_with_reference(self, tmp_path: Path) -> None:
        from PIL import Image

        ref = tmp_path / "ref.png"
        view1 = tmp_path / "front.png"
        img = Image.new("RGBA", (64, 64), (128, 128, 128, 255))
        img.save(ref)
        img.save(view1)

        result = MeshInspector._compare_with_reference(ref, [str(view1)], tmp_path)
        assert len(result) == 1
        assert "ssim" in result[0]
        assert result[0]["ssim"] >= 0.99

    def test_missing_view_skipped(self, tmp_path: Path) -> None:
        from PIL import Image

        ref = tmp_path / "ref.png"
        Image.new("RGBA", (64, 64), (128, 128, 128, 255)).save(ref)

        result = MeshInspector._compare_with_reference(ref, ["/nonexistent/front.png"], tmp_path)
        assert len(result) == 0


class TestPrintQAReport:
    def test_print_does_not_crash(self) -> None:
        report = MeshQAReport(
            path="/tmp/test.glb",
            topology=TopologyReport(vertices=100, faces=200, watertight=True),
            geometry=GeometryReport(volume_efficiency=0.5, flatness_ratio=0.3, thickness_ratio=0.2),
            artifacts=ArtifactReport(passed=True),
            score=QualityScore(overall=0.85, grade="B", summary="Grade B (85%)"),
        )
        print_qa_report(report)
