"""Testes do corte horizontal stump+top (Blender Z-up)."""

from __future__ import annotations

from pathlib import Path

import pytest

bpy = pytest.importorskip("bpy")

from gamedev_shared.bpy_mesh import clear_scene, face_count, get_bounds, load_glb, save_glb  # noqa: E402
from gamedev_shared.mesh_split import (  # noqa: E402
    resolve_cut_y,
    split_glb_at_height,
    split_mesh_object_at_height,
)


def _save_box_glb(path: Path, *, size: float = 2.0, location: tuple[float, float, float] = (0, 0, 1)) -> Path:
    """Cubo 2x2x2 centrado em ``location`` (default: Blender Z in [0, 2])."""
    clear_scene()
    bpy.ops.mesh.primitive_cube_add(size=size, location=location)
    obj = bpy.context.active_object
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    save_glb([obj], path)
    clear_scene()
    return path


class TestResolveCutY:
    def test_default_height(self) -> None:
        assert resolve_cut_y(0.0, 2.0) == pytest.approx(0.6)

    def test_cut_ratio(self) -> None:
        assert resolve_cut_y(0.0, 10.0, cut_ratio=0.25) == pytest.approx(2.5)

    def test_rejects_both(self) -> None:
        with pytest.raises(ValueError, match="não ambos"):
            resolve_cut_y(0.0, 2.0, cut_height=0.5, cut_ratio=0.3)

    def test_rejects_too_tall(self) -> None:
        with pytest.raises(ValueError, match=">="):
            resolve_cut_y(0.0, 0.5, cut_height=0.6)


class TestSplitMeshObject:
    def setup_method(self) -> None:
        clear_scene()

    def teardown_method(self) -> None:
        clear_scene()

    def test_splits_cube_into_two(self) -> None:
        # Cubo com altura no Z do Blender (0..2), como glTF Y-up após import.
        bpy.ops.mesh.primitive_cube_add(size=2.0, location=(0, 0, 1))
        obj = bpy.context.active_object
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
        stump, top = split_mesh_object_at_height(obj, 0.6, cap=True, bevel_segments=3)
        assert stump.name == "Stump"
        assert top.name == "Top"
        assert face_count(stump) >= 4
        assert face_count(top) >= 4
        _smin, smax = get_bounds(stump)
        tmin, tmax = get_bounds(top)
        # Tampão + bevel alargam um pouco além do plano de corte.
        assert smax[2] <= 0.6 + 0.35
        assert tmin[2] >= 0.6 - 0.35
        assert tmax[2] > smax[2]

        # Cubo: corte selado (faces horizontais no plano). Voxel morph pode
        # deixar ≤2 micro-edges residuais no canto — aceitável vs buraco aberto.
        import bmesh

        for half in (stump, top):
            bm = bmesh.new()
            bm.from_mesh(half.data)
            near = sum(1 for e in bm.edges if len(e.link_faces) == 1 and all(abs(v.co.z - 0.6) < 0.12 for v in e.verts))
            horiz = sum(
                1
                for f in bm.faces
                if abs(f.normal.z) > 0.8 and abs(sum(v.co.z for v in f.verts) / len(f.verts) - 0.6) < 0.15
            )
            bm.free()
            assert horiz >= 1, f"{half.name} sem faces de fecho no corte"
            # Clip à silhueta pode deixar micro-edges; buraco aberto = dezenas+.
            assert near <= 16, f"{half.name} ainda tem {near} boundary edges no corte"


class TestSplitGlb:
    def test_composition_and_split_files(self, tmp_path: Path) -> None:
        inp = _save_box_glb(tmp_path / "box.glb")
        out = tmp_path / "box_split.glb"
        result = split_glb_at_height(inp, out, cut_height=0.6, split_files=True)
        assert result.output.is_file()
        assert result.stump_path is not None and result.stump_path.is_file()
        assert result.top_path is not None and result.top_path.is_file()
        assert result.stump_faces >= 4
        assert result.top_faces >= 4

        objs = load_glb(out)
        names = {o.name for o in objs}
        assert "Stump" in names
        assert "Top" in names
        clear_scene()

    def test_inplace_overwrite(self, tmp_path: Path) -> None:
        path = _save_box_glb(tmp_path / "tree.glb")
        result = split_glb_at_height(path, path, cut_ratio=0.3)
        assert result.output.is_file()
        objs = load_glb(path)
        assert len(objs) == 2
        clear_scene()
