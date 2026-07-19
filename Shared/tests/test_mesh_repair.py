"""Tests for gamedev_shared.mesh_repair (unified bpy mesh-repair primitives)."""

from __future__ import annotations

import numpy as np
import pytest


class TestDropNonfiniteFaces:
    """Pure-numpy — runs without bpy."""

    def test_all_finite_passthrough(self) -> None:
        from gamedev_shared.mesh_repair import drop_nonfinite_faces

        verts = np.zeros((4, 3))
        faces = np.array([[0, 1, 2], [1, 2, 3]])
        _v, f, n = drop_nonfinite_faces(verts, faces)
        assert n == 0
        assert f.shape == (2, 3)

    def test_nan_vertex_drops_incident_faces(self) -> None:
        from gamedev_shared.mesh_repair import drop_nonfinite_faces

        verts = np.zeros((4, 3))
        verts[3, 1] = np.nan
        faces = np.array([[0, 1, 2], [1, 2, 3]])
        _v, f, n = drop_nonfinite_faces(verts, faces)
        assert n == 1
        np.testing.assert_array_equal(f, [[0, 1, 2]])

    def test_inf_vertex_drops_incident_faces(self) -> None:
        from gamedev_shared.mesh_repair import drop_nonfinite_faces

        verts = np.zeros((3, 3))
        verts[0, 0] = np.inf
        faces = np.array([[0, 1, 2]])
        _v, f, n = drop_nonfinite_faces(verts, faces)
        assert n == 1
        assert f.size == 0


@pytest.fixture(scope="module")
def _bpy():
    return pytest.importorskip("bpy")


class TestFixMesh:
    def test_removes_tiny_floater(self, _bpy) -> None:
        import trimesh

        from gamedev_shared.mesh_repair import fix_mesh

        main = trimesh.creation.box(extents=[1.0, 1.0, 1.0]).subdivide().subdivide()
        floater = trimesh.creation.box(extents=[0.01, 0.01, 0.01])
        floater.apply_translation([5.0, 5.0, 5.0])
        combined = trimesh.util.concatenate([main, floater])

        out = fix_mesh(combined)
        assert 0 < len(out.faces) < len(combined.faces)
        assert len(out.faces) >= len(main.faces) // 2

    def test_preserves_simple_box(self, _bpy) -> None:
        import trimesh

        from gamedev_shared.mesh_repair import fix_mesh

        out = fix_mesh(trimesh.creation.box(extents=[1.0, 1.0, 1.0]))
        assert len(out.faces) >= 8
        assert len(out.vertices) >= 8

    def test_empty_passthrough(self, _bpy) -> None:
        import trimesh

        from gamedev_shared.mesh_repair import fix_mesh

        empty = trimesh.Trimesh(vertices=np.zeros((0, 3)), faces=np.zeros((0, 3), dtype=np.int64), process=False)
        assert len(fix_mesh(empty).vertices) == 0

    def test_nan_fan_removed(self, _bpy) -> None:
        import trimesh

        from gamedev_shared.mesh_repair import fix_mesh

        box = trimesh.creation.box(extents=[1.0, 1.0, 1.0]).subdivide()
        verts = np.vstack([box.vertices, [[np.nan, np.nan, np.nan]]])
        nan_idx = len(verts) - 1
        fan = np.array([[0, 1, nan_idx], [1, 2, nan_idx], [2, 3, nan_idx]])
        faces = np.vstack([box.faces, fan])
        broken = trimesh.Trimesh(vertices=verts, faces=faces, process=False)

        out = fix_mesh(broken)
        assert np.isfinite(np.asarray(out.vertices)).all()
        assert len(out.faces) >= len(box.faces) // 2


class TestCapBoundaryHoles:
    @staticmethod
    def _open_edge_count(mesh) -> int:
        edges = np.sort(mesh.faces[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2), axis=1)
        _uniq, counts = np.unique(edges, axis=0, return_counts=True)
        return int((counts == 1).sum())

    def test_caps_small_planar_loop(self, _bpy) -> None:
        import trimesh

        from gamedev_shared.mesh_repair import cap_boundary_holes

        box = trimesh.creation.box(extents=[1.0, 1.0, 1.0]).subdivide()
        # Remove top half → open shell with a planar square boundary loop.
        keep = box.triangles_center[:, 2] < 0
        shell = trimesh.Trimesh(vertices=box.vertices, faces=box.faces[keep], process=False)
        shell.remove_unreferenced_vertices()

        capped = cap_boundary_holes(shell)
        assert self._open_edge_count(capped) < self._open_edge_count(shell)

    def test_giant_loop_skipped(self, _bpy) -> None:
        import trimesh

        from gamedev_shared.mesh_repair import cap_boundary_holes

        box = trimesh.creation.box(extents=[1.0, 1.0, 1.0]).subdivide()
        keep = box.triangles_center[:, 2] < 0
        shell = trimesh.Trimesh(vertices=box.vertices, faces=box.faces[keep], process=False)
        shell.remove_unreferenced_vertices()

        # max_loop_edges menor que o loop → nada tapado.
        capped = cap_boundary_holes(shell, max_loop_edges=3)
        assert self._open_edge_count(capped) == self._open_edge_count(shell)


class TestSanitizeNonfinite:
    def test_removes_nan_verts_inplace(self, _bpy) -> None:
        from gamedev_shared.bpy_mesh import clear_scene, create_mesh_from_arrays
        from gamedev_shared.mesh_repair import sanitize_nonfinite

        verts = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [np.nan, np.nan, np.nan]])
        faces = np.array([[0, 1, 2], [1, 2, 3]])
        clear_scene()
        obj = create_mesh_from_arrays(verts, faces.astype(np.int64), name="nan_test")
        removed = sanitize_nonfinite(obj)
        assert removed == 1
        assert len(obj.data.polygons) == 1  # face incidente ao NaN caiu
        assert sanitize_nonfinite(obj) == 0  # idempotente
        clear_scene()


class TestRepairGlb:
    def test_roundtrip_preserves_uv_material_and_rig(self, _bpy, tmp_path) -> None:
        import bpy

        from gamedev_shared.bpy_mesh import clear_scene, save_glb
        from gamedev_shared.mesh_repair import repair_glb

        clear_scene()
        # Cubo com UV + material + armature de 1 bone + vertex group.
        bpy.ops.mesh.primitive_cube_add()
        cube = bpy.context.active_object
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.uv.smart_project()
        bpy.ops.object.mode_set(mode="OBJECT")
        mat = bpy.data.materials.new("m0")
        mat.use_nodes = True
        cube.data.materials.append(mat)
        vg = cube.vertex_groups.new(name="Bone")
        vg.add(list(range(len(cube.data.vertices))), 1.0, "REPLACE")
        bpy.ops.object.armature_add()
        arm = bpy.context.active_object
        cube.parent = arm
        mod = cube.modifiers.new("Armature", "ARMATURE")
        mod.object = arm

        src = tmp_path / "rigged.glb"
        dst = tmp_path / "repaired.glb"
        save_glb(None, src)

        stats = repair_glb(src, dst)
        assert dst.exists() and dst.stat().st_size > 0
        assert stats  # pelo menos um mesh reparado

        # Reimportar e verificar preservação (TEMPERANCE: sem icosferas de
        # display de bones que o importer default acrescenta à cena).
        clear_scene()
        bpy.ops.import_scene.gltf(filepath=str(dst), bone_heuristic="TEMPERANCE")
        meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
        arms = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
        assert len(meshes) == 1 and len(arms) == 1
        m = meshes[0]
        assert m.data.uv_layers, "UVs perdidos no repair_glb"
        assert m.data.materials, "material perdido no repair_glb"
        assert m.vertex_groups, "skin weights perdidos no repair_glb"
        clear_scene()

    def test_shape_keys_skip_destructive(self, _bpy, tmp_path) -> None:
        import bpy

        from gamedev_shared.bpy_mesh import clear_scene
        from gamedev_shared.mesh_repair import repair_mesh_object

        clear_scene()
        bpy.ops.mesh.primitive_cube_add()
        cube = bpy.context.active_object
        cube.shape_key_add(name="Basis")
        cube.shape_key_add(name="Morph")
        n_verts = len(cube.data.vertices)
        stats = repair_mesh_object(cube)
        assert len(cube.data.vertices) == n_verts  # nada destrutivo correu
        assert "welded_exact" not in stats
        clear_scene()


class TestPrimitives:
    def test_remove_doubles_counts(self, _bpy) -> None:
        from gamedev_shared.bpy_mesh import clear_scene, create_mesh_from_arrays
        from gamedev_shared.mesh_repair import remove_doubles

        verts = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 0.00001]])
        faces = np.array([[0, 1, 2], [3, 1, 2]])
        clear_scene()
        obj = create_mesh_from_arrays(verts.astype(np.float64), faces.astype(np.int64), name="weld_test")
        removed = remove_doubles(obj, threshold=1e-3)
        assert removed == 1
        clear_scene()

    def test_remove_loose_debris_keeps_largest(self, _bpy) -> None:
        import trimesh

        from gamedev_shared.bpy_mesh import clear_scene, create_mesh_from_arrays
        from gamedev_shared.mesh_repair import remove_loose_debris

        # main grande o suficiente para tiny (12 faces) cair abaixo de
        # max(min_faces, face_ratio * total): 204 faces → threshold 20.
        main = trimesh.creation.box(extents=[1.0, 1.0, 1.0]).subdivide().subdivide()
        tiny = trimesh.creation.box(extents=[0.01, 0.01, 0.01])
        tiny.apply_translation([4.0, 0.0, 0.0])
        combined = trimesh.util.concatenate([main, tiny])
        clear_scene()
        obj = create_mesh_from_arrays(
            np.asarray(combined.vertices), np.asarray(combined.faces, dtype=np.int64), name="debris_test"
        )
        removed = remove_loose_debris(obj, face_ratio=0.1, min_faces=8)
        assert removed == len(tiny.faces)
        clear_scene()


class TestMakeWatertight:
    def test_open_bottom_box_closed(self, _bpy) -> None:
        import trimesh

        from gamedev_shared.bpy_mesh import clear_scene, create_mesh_from_arrays
        from gamedev_shared.mesh_repair import count_boundary_edges, make_watertight

        box = trimesh.creation.box(extents=[1.0, 1.0, 1.0]).subdivide()
        keep = np.abs(box.triangles_center[:, 2] + 0.5) > 1e-6  # remove fundo
        shell = trimesh.Trimesh(vertices=box.vertices, faces=box.faces[keep], process=False)
        shell.remove_unreferenced_vertices()
        clear_scene()
        obj = create_mesh_from_arrays(
            np.asarray(shell.vertices), np.asarray(shell.faces, dtype=np.int64), name="wt_test"
        )
        assert count_boundary_edges(obj) > 0
        stats = make_watertight(obj)
        assert stats["boundary_after"] == 0
        clear_scene()

    def test_base_cap_via_base_path(self, _bpy) -> None:
        import trimesh

        from gamedev_shared.bpy_mesh import clear_scene, create_mesh_from_arrays
        from gamedev_shared.mesh_repair import cap_boundary_loops, count_boundary_edges

        box = trimesh.creation.box(extents=[1.0, 1.0, 1.0]).subdivide()
        keep = np.abs(box.triangles_center[:, 2] + 0.5) > 1e-6
        shell = trimesh.Trimesh(vertices=box.vertices, faces=box.faces[keep], process=False)
        shell.remove_unreferenced_vertices()
        clear_scene()
        obj = create_mesh_from_arrays(
            np.asarray(shell.vertices), np.asarray(shell.faces, dtype=np.int64), name="base_test"
        )
        before = count_boundary_edges(obj)
        # max_loop_edges=3 bloqueia o caminho planar normal — só a regra de
        # base (sem limite de arestas) pode fechar o fundo.
        capped = cap_boundary_loops(obj, max_loop_edges=3, cap_base=True)
        assert capped >= 1
        assert count_boundary_edges(obj) < before
        clear_scene()

    def test_watertight_mesh_untouched(self, _bpy) -> None:
        import trimesh

        from gamedev_shared.bpy_mesh import clear_scene, create_mesh_from_arrays
        from gamedev_shared.mesh_repair import make_watertight

        box = trimesh.creation.box(extents=[1.0, 1.0, 1.0])
        clear_scene()
        obj = create_mesh_from_arrays(np.asarray(box.vertices), np.asarray(box.faces, dtype=np.int64), name="w0")
        n_faces = len(obj.data.polygons)
        stats = make_watertight(obj)
        assert stats["boundary_before"] == 0
        assert len(obj.data.polygons) == n_faces
        clear_scene()


class TestRepairProfiles:
    """Perfis nomeados — unificação topology / LOD / Part3D."""

    def test_dynamic_weld_distance_tiers(self) -> None:
        from gamedev_shared.mesh_repair import dynamic_weld_distance

        assert dynamic_weld_distance(200_000) == 0.003
        assert dynamic_weld_distance(120_000) == 0.005
        assert dynamic_weld_distance(60_000) == 0.008
        assert dynamic_weld_distance(10_000) == 0.01

    def test_unknown_profile_raises(self) -> None:
        from gamedev_shared.mesh_repair import get_repair_profile

        with pytest.raises(ValueError, match="desconhecido"):
            get_repair_profile("nope")

    def test_topology_clean_selective_watertight_flags(self) -> None:
        """Watertight seletivo: fecha rachas MC, sem shells/base/flare/Taubin."""
        from gamedev_shared.mesh_repair import get_repair_profile

        p = get_repair_profile("topology_clean")
        assert p.watertight is True
        assert p.watertight_cap_base is True
        assert p.watertight_final_fill is True
        assert p.watertight_skip_flap_erode is False
        assert p.watertight_max_loop_diameter_ratio == 0.35
        assert p.watertight_max_loop_edges == 400
        assert p.do_remove_internal_shells is False
        assert p.sliver_max_aspect == 80.0
        assert p.weld_mode == "vert_density"

    def test_pre_decimate_uv_no_watertight(self) -> None:
        from gamedev_shared.mesh_repair import get_repair_profile

        p = get_repair_profile("pre_decimate_uv")
        assert p.watertight is False
        assert p.weld_mode == "fixed"
        assert p.weld_fixed == 0.0005
        assert p.sliver_max_aspect == 80.0
        assert p.fill_holes_sides == 12

    def test_part_decode_aggressive_debris(self) -> None:
        from gamedev_shared.mesh_repair import get_repair_profile

        p = get_repair_profile("part_decode")
        assert p.debris_face_ratio == 0.1
        assert p.debris_min_faces == 8
        assert p.watertight is False

    def test_pre_decimate_profile_runs_without_watertight(self, _bpy) -> None:
        import trimesh

        from gamedev_shared.bpy_mesh import clear_scene, create_mesh_from_arrays
        from gamedev_shared.mesh_repair import repair_mesh_object_with_profile

        box = trimesh.creation.box(extents=[1.0, 1.0, 1.0]).subdivide()
        keep = np.abs(box.triangles_center[:, 2] + 0.5) > 1e-6
        shell = trimesh.Trimesh(vertices=box.vertices, faces=box.faces[keep], process=False)
        shell.remove_unreferenced_vertices()
        clear_scene()
        obj = create_mesh_from_arrays(
            np.asarray(shell.vertices), np.asarray(shell.faces, dtype=np.int64), name="pre_dec"
        )
        stats = repair_mesh_object_with_profile(obj, "pre_decimate_uv")
        # fill_holes(12) pode fechar loops pequenos; o importante é NÃO correr make_watertight.
        assert "boundary_before" not in stats
        assert "boundary_after" not in stats
        clear_scene()

    def test_topology_clean_opt_in_watertight_closes_open_shell(self, _bpy) -> None:
        import trimesh

        from gamedev_shared.bpy_mesh import clear_scene, create_mesh_from_arrays
        from gamedev_shared.mesh_repair import count_boundary_edges, repair_mesh_object_with_profile

        box = trimesh.creation.box(extents=[1.0, 1.0, 1.0]).subdivide()
        keep = np.abs(box.triangles_center[:, 2] + 0.5) > 1e-6
        shell = trimesh.Trimesh(vertices=box.vertices, faces=box.faces[keep], process=False)
        shell.remove_unreferenced_vertices()
        clear_scene()
        obj = create_mesh_from_arrays(np.asarray(shell.vertices), np.asarray(shell.faces, dtype=np.int64), name="topo")
        assert count_boundary_edges(obj) > 0
        # Perfil lean: watertight off; opt-in ainda fecha.
        stats = repair_mesh_object_with_profile(obj, "topology_clean", watertight=True)
        assert stats.get("boundary_after", 0) == 0
        clear_scene()

    def test_post_decimate_profile(self, _bpy) -> None:
        import trimesh

        from gamedev_shared.bpy_mesh import clear_scene, create_mesh_from_arrays
        from gamedev_shared.mesh_repair import get_repair_profile, repair_mesh_object_with_profile

        p = get_repair_profile("post_decimate")
        assert p.use_post_decimate_cleanup is True
        box = trimesh.creation.box(extents=[1.0, 1.0, 1.0])
        clear_scene()
        obj = create_mesh_from_arrays(np.asarray(box.vertices), np.asarray(box.faces, dtype=np.int64), name="pd")
        n_before = len(obj.data.polygons)
        stats = repair_mesh_object_with_profile(obj, "post_decimate")
        assert stats.get("profile_post_decimate") == 1
        assert len(obj.data.polygons) >= n_before // 2
        clear_scene()

    def test_topology_clean_does_reweld_flag(self) -> None:
        from gamedev_shared.mesh_repair import get_repair_profile

        assert get_repair_profile("topology_clean").do_reweld_coincident is True
        assert get_repair_profile("pre_decimate_uv").do_reweld_coincident is False


class TestRepairWatertightRoundtrip:
    """Fecho watertight em meshes geradas no teste (sem assets externos)."""

    @staticmethod
    def _procedural_open_box(*, size: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
        """Caixa unitária triangulada **sem a face do fundo** (abertura em -Z).

        8 vértices, 10 triângulos (5 faces x 2). O loop de fronteira do fundo
        tem 4 arestas — fechado por ``make_watertight`` (opt-in no perfil lean).
        """
        s = size / 2.0
        #      4----5
        #     /|   /|
        #    7----6 |
        #    | 0--|-1   (+Z up, -Z = fundo)
        #    |/   |/
        #    3----2
        verts = np.array(
            [
                [-s, -s, -s],  # 0
                [s, -s, -s],  # 1
                [s, s, -s],  # 2
                [-s, s, -s],  # 3
                [-s, -s, s],  # 4
                [s, -s, s],  # 5
                [s, s, s],  # 6
                [-s, s, s],  # 7
            ],
            dtype=np.float64,
        )
        # Sem fundo (0,1,2,3). Normais outward aproximadas.
        faces = np.array(
            [
                # +Z top
                [4, 5, 6],
                [4, 6, 7],
                # +Y
                [3, 2, 6],
                [3, 6, 7],
                # -Y
                [0, 1, 5],
                [0, 5, 4],
                # +X
                [1, 2, 6],
                [1, 6, 5],
                # -X
                [0, 3, 7],
                [0, 7, 4],
            ],
            dtype=np.int64,
        )
        return verts, faces

    @staticmethod
    def _procedural_open_box_subdivided() -> tuple[np.ndarray, np.ndarray]:
        """Caixa aberta com subdivisão (trimesh) — mais arestas no loop do fundo."""
        import trimesh

        box = trimesh.creation.box(extents=[1.0, 1.0, 1.0]).subdivide()
        keep = np.abs(box.triangles_center[:, 2] + 0.5) > 1e-6
        shell = trimesh.Trimesh(vertices=box.vertices, faces=box.faces[keep], process=False)
        shell.remove_unreferenced_vertices()
        return np.asarray(shell.vertices, dtype=np.float64), np.asarray(shell.faces, dtype=np.int64)

    @staticmethod
    def _boundary_edge_count_numpy(faces: np.ndarray) -> int:
        edges = np.sort(faces[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2), axis=1)
        _uniq, counts = np.unique(edges, axis=0, return_counts=True)
        return int((counts == 1).sum())

    def test_procedural_open_box_has_boundary_before_repair(self, _bpy) -> None:
        from gamedev_shared.bpy_mesh import clear_scene, create_mesh_from_arrays
        from gamedev_shared.mesh_repair import count_boundary_edges

        verts, faces = self._procedural_open_box()
        clear_scene()
        obj = create_mesh_from_arrays(verts, faces, name="open_box")
        assert len(obj.data.polygons) == 10
        assert count_boundary_edges(obj) == 4  # loop do fundo
        clear_scene()

    def test_make_watertight_closes_procedural_open_box(self, _bpy) -> None:
        from gamedev_shared.bpy_mesh import clear_scene, create_mesh_from_arrays
        from gamedev_shared.mesh_repair import count_boundary_edges, make_watertight

        verts, faces = self._procedural_open_box()
        clear_scene()
        obj = create_mesh_from_arrays(verts, faces, name="wt_box")
        before = count_boundary_edges(obj)
        assert before > 0
        faces_before = len(obj.data.polygons)
        stats = make_watertight(obj)
        assert stats["boundary_before"] == before
        assert stats["boundary_after"] == 0
        assert count_boundary_edges(obj) == 0
        assert len(obj.data.polygons) > faces_before  # caps acrescentaram faces
        clear_scene()

    def test_topology_clean_opt_in_watertight_closes_procedural_open_box(self, _bpy) -> None:
        from gamedev_shared.bpy_mesh import clear_scene, create_mesh_from_arrays
        from gamedev_shared.mesh_repair import count_boundary_edges, repair_mesh_object_with_profile

        verts, faces = self._procedural_open_box()
        clear_scene()
        obj = create_mesh_from_arrays(verts, faces, name="topo_box")
        assert count_boundary_edges(obj) > 0
        stats = repair_mesh_object_with_profile(obj, "topology_clean", watertight=True)
        assert stats.get("boundary_after") == 0
        assert count_boundary_edges(obj) == 0
        assert len(obj.data.polygons) >= 10
        clear_scene()

    def test_topology_clean_opt_in_watertight_closes_subdivided_open_shell(self, _bpy) -> None:
        from gamedev_shared.bpy_mesh import clear_scene, create_mesh_from_arrays
        from gamedev_shared.mesh_repair import count_boundary_edges, repair_mesh_object_with_profile

        verts, faces = self._procedural_open_box_subdivided()
        clear_scene()
        obj = create_mesh_from_arrays(verts, faces, name="topo_sub")
        assert count_boundary_edges(obj) > 0
        faces_before = len(obj.data.polygons)
        stats = repair_mesh_object_with_profile(obj, "topology_clean", watertight=True)
        assert stats.get("boundary_after") == 0
        assert count_boundary_edges(obj) == 0
        assert len(obj.data.polygons) >= faces_before
        clear_scene()

    def test_topology_clean_runs_selective_watertight(self, _bpy) -> None:
        """Watertight seletivo corre no topology_clean (stats de boundary presentes)."""
        from gamedev_shared.bpy_mesh import clear_scene, create_mesh_from_arrays
        from gamedev_shared.mesh_repair import repair_mesh_object_with_profile

        verts, faces = self._procedural_open_box_subdivided()
        clear_scene()
        obj = create_mesh_from_arrays(verts, faces, name="topo_wt")
        stats = repair_mesh_object_with_profile(obj, "topology_clean")
        assert "boundary_before" in stats
        assert "boundary_after" in stats
        assert stats["boundary_after"] <= stats["boundary_before"]
        clear_scene()

    def test_repair_mesh_object_watertight_closes(self, _bpy) -> None:
        from gamedev_shared.bpy_mesh import clear_scene, create_mesh_from_arrays
        from gamedev_shared.mesh_repair import count_boundary_edges, repair_mesh_object

        verts, faces = self._procedural_open_box_subdivided()
        clear_scene()
        obj = create_mesh_from_arrays(verts, faces, name="rmo_wt")
        assert count_boundary_edges(obj) > 0
        stats = repair_mesh_object(obj, watertight=True, fill_holes_sides=12)
        assert stats.get("boundary_after") == 0
        assert count_boundary_edges(obj) == 0
        clear_scene()

    def test_repair_glb_watertight_survives_roundtrip(self, _bpy, tmp_path) -> None:
        from gamedev_shared.bpy_mesh import clear_scene, create_mesh_from_arrays, save_glb
        from gamedev_shared.mesh_repair import count_boundary_edges, repair_glb

        verts, faces = self._procedural_open_box_subdivided()
        clear_scene()
        obj = create_mesh_from_arrays(verts, faces, name="shell")
        src = tmp_path / "open.glb"
        dst = tmp_path / "closed.glb"
        save_glb([obj], src, export_normals=False, export_tangents=False)

        stats = repair_glb(src, dst, watertight=True)
        assert dst.exists()
        mesh_stats = next(iter(stats.values()))
        assert mesh_stats.get("boundary_after") == 0

        clear_scene()
        import bpy

        bpy.ops.import_scene.gltf(filepath=str(dst), bone_heuristic="TEMPERANCE")
        meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
        assert len(meshes) == 1
        assert count_boundary_edges(meshes[0]) == 0
        assert len(meshes[0].data.polygons) > 0
        clear_scene()

    def test_fix_mesh_watertight_closes_open_shell(self, _bpy) -> None:
        import trimesh

        from gamedev_shared.mesh_repair import fix_mesh

        verts, faces = self._procedural_open_box_subdivided()
        shell = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
        out = fix_mesh(shell, watertight=True)
        assert self._boundary_edge_count_numpy(np.asarray(out.faces)) == 0
        assert len(out.faces) > len(shell.faces)  # caps added

    def test_fix_mesh_watertight_false_preserves_opening(self, _bpy) -> None:
        import trimesh

        from gamedev_shared.mesh_repair import fix_mesh

        verts, faces = self._procedural_open_box()
        shell = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
        out = fix_mesh(shell, watertight=False, fill_holes_sides=0)
        assert self._boundary_edge_count_numpy(np.asarray(out.faces)) > 0

    def test_pre_decimate_uv_does_not_force_watertight(self, _bpy) -> None:
        """Perfil LOD: pode fechar loops ≤12, mas não corre make_watertight."""
        from gamedev_shared.bpy_mesh import clear_scene, create_mesh_from_arrays
        from gamedev_shared.mesh_repair import repair_mesh_object_with_profile

        # Caixa aberta simples: loop de 4 arestas — fill_holes(12) pode tapar,
        # mas stats não devem ter boundary_before/after do make_watertight.
        verts, faces = self._procedural_open_box()
        clear_scene()
        obj = create_mesh_from_arrays(verts, faces, name="pre_uv")
        stats = repair_mesh_object_with_profile(obj, "pre_decimate_uv")
        assert "boundary_before" not in stats
        assert "boundary_after" not in stats
        clear_scene()


class TestClampBaseFlareAndTaubin:
    def test_topology_clean_no_destructive_steps(self) -> None:
        """Sem shells/force_base/flare/Taubin (destruíam edifícios casca-plástico)."""
        from gamedev_shared.mesh_repair import get_repair_profile

        p = get_repair_profile("topology_clean")
        assert p.fill_holes_sides == 96
        assert p.do_remove_internal_shells is False
        assert p.watertight is True
        assert p.force_close_base is False
        assert p.do_clamp_base_flare is False
        assert p.do_taubin is False

    @staticmethod
    def _cylinder_y(
        radius: float,
        y0: float,
        y1: float,
        *,
        sections: int = 16,
        rings: int = 8,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Cilindro Y-up com vários anéis (necessário p/ banda mid do clamp)."""
        angs = np.linspace(0, 2 * np.pi, sections, endpoint=False)
        ys = np.linspace(y0, y1, rings)
        verts = []
        for y in ys:
            verts.append(np.stack([radius * np.cos(angs), np.full(sections, y), radius * np.sin(angs)], axis=1))
        verts_arr = np.vstack(verts)
        faces: list[list[int]] = []
        for r in range(rings - 1):
            base = r * sections
            nxt = (r + 1) * sections
            for i in range(sections):
                j = (i + 1) % sections
                a, b, c, d = base + i, base + j, nxt + i, nxt + j
                faces.append([a, b, d])
                faces.append([a, d, c])
        return verts_arr.astype(np.float64), np.asarray(faces, dtype=np.int64)

    def test_clamp_base_flare_pulls_elephant_feet(self, _bpy) -> None:
        from gamedev_shared.bpy_mesh import clear_scene, create_mesh_from_arrays
        from gamedev_shared.mesh_repair import clamp_base_flare

        v_body, f_body = self._cylinder_y(0.30, 0.20, 2.0, rings=10)
        v_foot, f_foot = self._cylinder_y(0.55, 0.0, 0.15, rings=3)
        verts = np.vstack([v_body, v_foot])
        faces = np.vstack([f_body, f_foot + len(v_body)])
        clear_scene()
        obj = create_mesh_from_arrays(verts, faces, name="flare")
        before = np.array([v.co[:] for v in obj.data.vertices], dtype=np.float64)
        y_lo = float(before[:, 1].min())
        h = float(before[:, 1].max() - y_lo)
        bot = before[before[:, 1] <= y_lo + 0.12 * h]
        r_before = float(np.linalg.norm(bot[:, [0, 2]], axis=1).max())
        moved = clamp_base_flare(obj, max_flare_ratio=1.05, bottom_frac=0.12)
        assert moved > 0
        after = np.array([v.co[:] for v in obj.data.vertices], dtype=np.float64)
        bot_a = after[after[:, 1] <= y_lo + 0.12 * h]
        r_after = float(np.linalg.norm(bot_a[:, [0, 2]], axis=1).max())
        assert r_after < r_before
        clear_scene()

    def test_taubin_smooth_runs(self, _bpy) -> None:
        from gamedev_shared.bpy_mesh import clear_scene, create_mesh_from_arrays
        from gamedev_shared.mesh_repair import taubin_smooth

        # Malha densa o suficiente (taubin exige ≥8 verts).
        verts, faces = self._cylinder_y(0.5, -0.5, 0.5, sections=12, rings=4)
        clear_scene()
        obj = create_mesh_from_arrays(verts, faces, name="cyl")
        n = taubin_smooth(obj, iterations=2)
        assert n == 2
        clear_scene()


class TestForceCloseBase:
    """Laje forçada: shell oco (sem chão) vs caixa sólida."""

    @staticmethod
    def _open_bottom_box() -> tuple[np.ndarray, np.ndarray]:
        """Caixa unitária Y-up sem face inferior (oco por baixo)."""
        verts = np.array(
            [
                [-0.5, 0.0, -0.5],
                [0.5, 0.0, -0.5],
                [0.5, 0.0, 0.5],
                [-0.5, 0.0, 0.5],
                [-0.5, 1.0, -0.5],
                [0.5, 1.0, -0.5],
                [0.5, 1.0, 0.5],
                [-0.5, 1.0, 0.5],
            ],
            dtype=np.float64,
        )
        # top + 4 walls (no bottom 0-1-2-3)
        faces = np.array(
            [
                [4, 5, 6],
                [4, 6, 7],
                [0, 1, 5],
                [0, 5, 4],
                [1, 2, 6],
                [1, 6, 5],
                [2, 3, 7],
                [2, 7, 6],
                [3, 0, 4],
                [3, 4, 7],
            ],
            dtype=np.int64,
        )
        return verts, faces

    def test_force_close_base_seals_open_bottom(self, _bpy) -> None:
        from gamedev_shared.bpy_mesh import clear_scene, create_mesh_from_arrays
        from gamedev_shared.mesh_repair import base_openness_stats, force_close_base

        verts, faces = self._open_bottom_box()
        clear_scene()
        obj = create_mesh_from_arrays(verts, faces, name="open_box")
        h_before = float(obj.dimensions.y)
        before = base_openness_stats(obj, up_axis=1, grid=24)
        assert before["recess_ratio"] >= 0.50
        stats = force_close_base(obj, up_axis=1, grid=24, recess_trigger=0.50, min_cells=4, min_faces=1)
        assert stats["base_forced_faces"] > 0
        assert stats.get("base_rollback", 0) == 0
        after = base_openness_stats(obj, up_axis=1, grid=24)
        assert after["recess_ratio"] < before["recess_ratio"]
        assert float(obj.dimensions.y) <= h_before * 1.02
        clear_scene()

    def test_force_close_base_skips_below_trigger(self, _bpy) -> None:
        """Recess abaixo do limiar → no-op (torre sólida ~0.01 no eixo mundo)."""
        from gamedev_shared.bpy_mesh import clear_scene, create_mesh_from_arrays
        from gamedev_shared.mesh_repair import force_close_base

        verts, faces = self._open_bottom_box()
        clear_scene()
        obj = create_mesh_from_arrays(verts, faces, name="open")
        # recess caixa aberta ≈1.0; trigger >1 → skip
        stats = force_close_base(obj, up_axis=1, grid=24, recess_trigger=1.01, min_cells=4, min_faces=1)
        assert stats["base_forced_faces"] == 0
        clear_scene()

    def test_force_close_base_skips_solid_box(self, _bpy) -> None:
        from gamedev_shared.bpy_mesh import clear_scene, create_mesh_from_arrays
        from gamedev_shared.mesh_repair import force_close_base

        # Caixa completa (com fundo)
        verts = np.array(
            [
                [-0.5, 0.0, -0.5],
                [0.5, 0.0, -0.5],
                [0.5, 0.0, 0.5],
                [-0.5, 0.0, 0.5],
                [-0.5, 1.0, -0.5],
                [0.5, 1.0, -0.5],
                [0.5, 1.0, 0.5],
                [-0.5, 1.0, 0.5],
            ],
            dtype=np.float64,
        )
        faces = np.array(
            [
                [0, 1, 2],
                [0, 2, 3],
                [4, 5, 6],
                [4, 6, 7],
                [0, 1, 5],
                [0, 5, 4],
                [1, 2, 6],
                [1, 6, 5],
                [2, 3, 7],
                [2, 7, 6],
                [3, 0, 4],
                [3, 4, 7],
            ],
            dtype=np.int64,
        )
        clear_scene()
        obj = create_mesh_from_arrays(verts, faces, name="solid")
        stats = force_close_base(obj, up_axis=1, grid=24, recess_trigger=0.25, min_faces=1)
        assert stats["base_forced_faces"] == 0
        clear_scene()


class TestRemoveInternalShellFaces:
    def test_solid_box_untouched(self, _bpy) -> None:
        from gamedev_shared.bpy_mesh import clear_scene, create_mesh_from_arrays
        from gamedev_shared.mesh_repair import normals_consistent, remove_internal_shell_faces

        verts = np.array(
            [
                [-0.5, -0.5, -0.5],
                [0.5, -0.5, -0.5],
                [0.5, 0.5, -0.5],
                [-0.5, 0.5, -0.5],
                [-0.5, -0.5, 0.5],
                [0.5, -0.5, 0.5],
                [0.5, 0.5, 0.5],
                [-0.5, 0.5, 0.5],
            ],
            dtype=np.float64,
        )
        faces = np.array(
            [
                [0, 1, 2],
                [0, 2, 3],
                [4, 6, 5],
                [4, 7, 6],
                [0, 4, 5],
                [0, 5, 1],
                [2, 6, 7],
                [2, 7, 3],
                [0, 3, 7],
                [0, 7, 4],
                [1, 5, 6],
                [1, 6, 2],
            ],
            dtype=np.int64,
        )
        clear_scene()
        obj = create_mesh_from_arrays(verts, faces, name="solid")
        normals_consistent(obj)
        before = len(obj.data.polygons)
        removed = remove_internal_shell_faces(obj)
        assert removed == 0
        assert len(obj.data.polygons) == before
        clear_scene()

    def test_double_shell_removes_inner(self, _bpy) -> None:
        """Outer box + inner box (normais para o oco) → remove faces da casca interna."""
        from gamedev_shared.bpy_mesh import clear_scene, create_mesh_from_arrays
        from gamedev_shared.mesh_repair import remove_internal_shell_faces

        def _box(lo: float, hi: float, *, flip: bool) -> tuple[np.ndarray, np.ndarray]:
            verts = np.array(
                [
                    [lo, lo, lo],
                    [hi, lo, lo],
                    [hi, hi, lo],
                    [lo, hi, lo],
                    [lo, lo, hi],
                    [hi, lo, hi],
                    [hi, hi, hi],
                    [lo, hi, hi],
                ],
                dtype=np.float64,
            )
            faces = np.array(
                [
                    [0, 1, 2],
                    [0, 2, 3],
                    [4, 6, 5],
                    [4, 7, 6],
                    [0, 4, 5],
                    [0, 5, 1],
                    [2, 6, 7],
                    [2, 7, 3],
                    [0, 3, 7],
                    [0, 7, 4],
                    [1, 5, 6],
                    [1, 6, 2],
                ],
                dtype=np.int64,
            )
            if flip:
                faces = faces[:, ::-1].copy()
            return verts, faces

        clear_scene()
        # Ambas outward (caso Hunyuan): casca interna olha para o vão fino.
        vo, fo = _box(-1.0, 1.0, flip=True)
        vi, fi = _box(-0.85, 0.85, flip=True)
        outer = create_mesh_from_arrays(vo, fo, name="outer")
        inner = create_mesh_from_arrays(vi, fi, name="inner")
        import bpy

        bpy.ops.object.select_all(action="DESELECT")
        outer.select_set(True)
        inner.select_set(True)
        bpy.context.view_layer.objects.active = outer
        bpy.ops.object.join()
        obj = bpy.context.active_object
        before = len(obj.data.polygons)
        removed = remove_internal_shell_faces(obj, wall_gap_ratio=0.25, room_gap_ratio=0.9, max_removal_ratio=0.75)
        assert removed > 0, f"expected inner shell removal, got {removed}"
        assert removed <= before // 2 + 2
        assert len(obj.data.polygons) < before
        clear_scene()

    def test_prop_inside_hollow_room_untouched(self, _bpy) -> None:
        """Objecto no oco (sino) — gap longo, NÃO sanduíche fino → 0 removidos."""
        from gamedev_shared.bpy_mesh import clear_scene, create_mesh_from_arrays
        from gamedev_shared.mesh_repair import remove_internal_shell_faces

        def _box(lo: float, hi: float, *, flip: bool) -> tuple[np.ndarray, np.ndarray]:
            verts = np.array(
                [
                    [lo, lo, lo],
                    [hi, lo, lo],
                    [hi, hi, lo],
                    [lo, hi, lo],
                    [lo, lo, hi],
                    [hi, lo, hi],
                    [hi, hi, hi],
                    [lo, hi, hi],
                ],
                dtype=np.float64,
            )
            faces = np.array(
                [
                    [0, 1, 2],
                    [0, 2, 3],
                    [4, 6, 5],
                    [4, 7, 6],
                    [0, 4, 5],
                    [0, 5, 1],
                    [2, 6, 7],
                    [2, 7, 3],
                    [0, 3, 7],
                    [0, 7, 4],
                    [1, 5, 6],
                    [1, 6, 2],
                ],
                dtype=np.int64,
            )
            if flip:
                faces = faces[:, ::-1].copy()
            return verts, faces

        clear_scene()
        # Casca exterior + prop pequeno no centro (gap >> wall_gap).
        vo, fo = _box(-2.0, 2.0, flip=True)
        vp, fp = _box(-0.15, 0.15, flip=True)
        outer = create_mesh_from_arrays(vo, fo, name="room")
        prop = create_mesh_from_arrays(vp, fp, name="bell")
        import bpy

        bpy.ops.object.select_all(action="DESELECT")
        outer.select_set(True)
        prop.select_set(True)
        bpy.context.view_layer.objects.active = outer
        bpy.ops.object.join()
        obj = bpy.context.active_object
        before = len(obj.data.polygons)
        removed = remove_internal_shell_faces(obj, wall_gap_ratio=0.08, max_removal_ratio=0.75)
        assert removed == 0, f"prop no oco não deve ser apagado, got {removed}"
        assert len(obj.data.polygons) == before
        clear_scene()


class TestCapBoundaryDiameterGuard:
    def test_large_opening_not_capped(self, _bpy) -> None:
        """Abertura grande (porta): max_loop_diameter_ratio bloqueia o cap."""
        from gamedev_shared.bpy_mesh import clear_scene, create_mesh_from_arrays
        from gamedev_shared.mesh_repair import cap_boundary_loops, count_boundary_edges

        # Caixa sem topo — loop de 4 arestas, diâmetro ~√2 na face 1x1.
        verts = np.array(
            [
                [-0.5, -0.5, -0.5],
                [0.5, -0.5, -0.5],
                [0.5, 0.5, -0.5],
                [-0.5, 0.5, -0.5],
                [-0.5, -0.5, 0.5],
                [0.5, -0.5, 0.5],
                [0.5, 0.5, 0.5],
                [-0.5, 0.5, 0.5],
            ],
            dtype=np.float64,
        )
        faces = np.array(
            [
                [0, 1, 2],
                [0, 2, 3],  # bottom
                [0, 4, 5],
                [0, 5, 1],
                [1, 5, 6],
                [1, 6, 2],
                [2, 6, 7],
                [2, 7, 3],
                [3, 7, 4],
                [3, 4, 0],
                # sem topo
            ],
            dtype=np.int64,
        )
        clear_scene()
        obj = create_mesh_from_arrays(verts, faces, name="open_box")
        assert count_boundary_edges(obj) > 0
        capped = cap_boundary_loops(obj, max_loop_edges=32, planar_tol=0.15, max_loop_diameter_ratio=0.08)
        assert capped == 0
        assert count_boundary_edges(obj) > 0
        # Sem guarda de diâmetro, o loop pequeno+planar tapa.
        capped2 = cap_boundary_loops(obj, max_loop_edges=32, planar_tol=0.15)
        assert capped2 >= 1
        clear_scene()
