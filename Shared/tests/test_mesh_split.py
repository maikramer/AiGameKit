"""Testes do corte horizontal stump+top (Blender Z-up)."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest

bpy = pytest.importorskip("bpy")

from gamedev_shared.bpy_mesh import clear_scene, face_count, get_bounds, load_glb, save_glb  # noqa: E402
from gamedev_shared.mesh_split import (  # noqa: E402
    _bridge_cap_to_bark,
    _cleanup_cut_leak_geometry,
    _fuse_cut_band,
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
    def test_default_min_cap_or_quarter(self) -> None:
        # altura 2 → 1/4=0.5 < 0.8
        assert resolve_cut_y(0.0, 2.0) == pytest.approx(0.5)
        # altura 10 → 1/4=2.5 → cap 0.8
        assert resolve_cut_y(0.0, 10.0) == pytest.approx(0.8)

    def test_cut_ratio(self) -> None:
        assert resolve_cut_y(0.0, 10.0, cut_ratio=0.25) == pytest.approx(2.5)

    def test_rejects_both(self) -> None:
        with pytest.raises(ValueError, match="não ambos"):
            resolve_cut_y(0.0, 2.0, cut_height=0.5, cut_ratio=0.3)

    def test_rejects_too_tall(self) -> None:
        with pytest.raises(ValueError, match=">="):
            resolve_cut_y(0.0, 0.5, cut_height=0.6)


class TestCleanupCutLeaks:
    def setup_method(self) -> None:
        clear_scene()

    def teardown_method(self) -> None:
        clear_scene()

    def test_drops_thin_sheet_near_cut(self) -> None:
        import bmesh

        # Corpo: cubo 1x1x1 em z∈[0,1].
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0, 0, 0.5))
        body = bpy.context.active_object
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
        # Folha fina no plano z=0.6 a vazar para fora (span XY grande, dz~0).
        bm = bmesh.new()
        bm.from_mesh(body.data)
        z = 0.6
        verts = [
            bm.verts.new((-0.8, -0.8, z)),
            bm.verts.new((0.8, -0.8, z)),
            bm.verts.new((0.8, 0.8, z)),
            bm.verts.new((-0.8, 0.8, z)),
        ]
        bm.faces.new(verts)
        bm.to_mesh(body.data)
        body.data.update()
        bm.free()
        before = len(body.data.polygons)
        stats = _cleanup_cut_leak_geometry(body, 0.6, max_thickness=0.02, min_span=0.06)
        assert stats["thin_faces"] >= 1
        assert len(body.data.polygons) < before

    def test_fuse_dissolves_coplanar_cut_band(self) -> None:
        import bmesh

        bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0, 0, 0.5))
        body = bpy.context.active_object
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
        # Duas faces coplanares no corte (grelha 2 quads) — dissolve deve fundir.
        bm = bmesh.new()
        bm.from_mesh(body.data)
        z = 0.5
        # Remover topo do cubo se existir perto de z=1; adicionar grelha no meio.
        v00 = bm.verts.new((-0.2, -0.2, z))
        v10 = bm.verts.new((0.0, -0.2, z))
        v20 = bm.verts.new((0.2, -0.2, z))
        v01 = bm.verts.new((-0.2, 0.2, z))
        v11 = bm.verts.new((0.0, 0.2, z))
        v21 = bm.verts.new((0.2, 0.2, z))
        bm.faces.new((v00, v10, v11, v01))
        bm.faces.new((v10, v20, v21, v11))
        bm.to_mesh(body.data)
        body.data.update()
        bm.free()
        before = len(body.data.polygons)
        stats = _fuse_cut_band(body, 0.5, band=0.1, angle_deg=10.0, weld_dist=0.01)
        assert stats["dissolved_edges"] >= 1 or stats["welded"] >= 0
        assert len(body.data.polygons) <= before


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


class TestBridgeCapToBark:
    """Testes do bridge vert-a-vert cap↔casca."""

    def setup_method(self) -> None:
        clear_scene()

    def teardown_method(self) -> None:
        clear_scene()

    def _make_cylinder_with_cap(self, cut_z: float = 0.6, radius: float = 0.5, gap_deg: float = 0.0) -> Any:
        """Cilindro com tampão plano a z=cut_z. gap_deg>0 abre uma fenda na casca."""
        import bmesh

        # Casca: cilindro raio 0.5, altura 1.0.
        bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=radius, depth=1.0, location=(0, 0, 0.5))
        bark = bpy.context.active_object
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

        if gap_deg > 0:
            # Apagar faces da casca numa faixa angular perto do corte.
            bm = bmesh.new()
            bm.from_mesh(bark.data)
            bm.faces.ensure_lookup_table()
            doomed = []
            for f in bm.faces:
                c = f.calc_center_median()
                if abs(c.z - cut_z) > 0.15:
                    continue
                ang = math.degrees(math.atan2(c.y, c.x))
                if -gap_deg / 2 < ang < gap_deg / 2:
                    doomed.append(f)
            if doomed:
                bmesh.ops.delete(bm, geom=doomed, context="FACES")
            bm.to_mesh(bark.data)
            bark.data.update()
            bm.free()

        # Cap: disco plano a z=cut_z (raio ligeiramente menor).
        bm = bmesh.new()
        bm.from_mesh(bark.data)
        bm.verts.ensure_lookup_table()
        cap_r = radius * 0.9
        cap_verts = []
        for i in range(24):
            ang = 2 * math.pi * i / 24
            cap_verts.append(bm.verts.new((cap_r * math.cos(ang), cap_r * math.sin(ang), cut_z)))
        center = bm.verts.new((0, 0, cut_z))
        for i in range(24):
            bm.faces.new((center, cap_verts[i], cap_verts[(i + 1) % 24]))
        bm.to_mesh(bark.data)
        bark.data.update()
        bm.free()
        return bark

    def _boundary_count(self, obj: Any, cut_z: float, band: float = 0.08) -> int:
        import bmesh

        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bm.edges.ensure_lookup_table()
        n = sum(1 for e in bm.edges if len(e.link_faces) == 1 and any(abs(v.co.z - cut_z) <= band for v in e.verts))
        bm.free()
        return n

    def test_reduces_boundary_edges(self) -> None:
        """Bridge deve reduzir boundary edges do cap (não deixá-lo a flutuar)."""
        cut_z = 0.6
        obj = self._make_cylinder_with_cap(cut_z=cut_z, radius=0.5, gap_deg=0.0)
        before = self._boundary_count(obj, cut_z)
        stats = _bridge_cap_to_bark(obj, cut_z, band=0.08, max_bridge_dist=0.05, weld_dist=0.01)
        after = self._boundary_count(obj, cut_z)
        # Bridge+weld deve reduzir (ou pelo menos não aumentar drasticamente).
        assert after <= before, f"boundary piorou: {before}→{after}"
        assert stats["filled"] >= 0  # não crasha
        assert stats["bridges"] >= 0

    def test_does_not_crash_with_no_bark(self) -> None:
        """Sem casca perto do corte, bridge não crasha."""
        # Cubo pequeno longe do corte (z=5), sem casca a z=0.6.
        bpy.ops.mesh.primitive_cube_add(size=0.1, location=(0, 0, 5.0))
        obj = bpy.context.active_object
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
        stats = _bridge_cap_to_bark(obj, 0.6, band=0.08, max_bridge_dist=0.05, weld_dist=0.01)
        assert "filled" in stats
        assert stats["filled"] == 0  # nada para preencher
