"""Tests for aigamekit_shared.mesh_simplify — unified Decimate COLLAPSE pipeline."""

from __future__ import annotations

from pathlib import Path

import pytest

from aigamekit_shared.mesh_simplify import (
    _MIN_DECIMATE_FACES,
    _split_face_budget,
    clamp_decimate_target,
)


class TestClampDecimateTarget:
    def test_target_above_floor_kept(self) -> None:
        assert clamp_decimate_target(100_000, 5000) == 5000

    def test_target_below_floor_raised(self) -> None:
        # floor = max(150, 1_000_000 * 0.0005) = 500
        assert clamp_decimate_target(1_000_000, 100) == 500

    def test_small_mesh_min_faces_floor(self) -> None:
        # floor = max(150, 10_000 * 0.0005 = 5) = 150
        assert clamp_decimate_target(10_000, 50) == _MIN_DECIMATE_FACES

    def test_lod_ladder_budget_not_overridden(self) -> None:
        """O piso relativo não pode achatar a ladder de um prop pequeno.

        Regressão: com _MIN_DECIMATE_FRAC=0.008 o baú (174k faces pintadas)
        via lod0/1/2 todos subidos a 1394 → três meshes iguais em disco.
        """
        n = 174_258
        targets = [clamp_decimate_target(n, t) for t in (8712, 4356, 2904)]
        assert targets == [8712, 4356, 2904]
        assert len(set(targets)) == 3

    def test_target_above_face_count_passthrough(self) -> None:
        assert clamp_decimate_target(100, 500) == 500

    def test_minimum_four_faces(self) -> None:
        assert clamp_decimate_target(2, 1) == 4


class TestSplitFaceBudget:
    class _FakeObj:
        def __init__(self, n_faces: int) -> None:
            class _Data:
                polygons = [None] * n_faces

            self.data = _Data()

    def test_proportional_split(self) -> None:
        objs = [self._FakeObj(900), self._FakeObj(100)]
        budgets = _split_face_budget(objs, 1000)
        assert budgets == [900, 100]

    def test_minimum_per_object(self) -> None:
        objs = [self._FakeObj(10_000), self._FakeObj(1)]
        budgets = _split_face_budget(objs, 100)
        assert budgets[1] >= 4


# ---------------------------------------------------------------------------
# bpy-dependent tests (auto-skip without bpy)
# ---------------------------------------------------------------------------

bpy = pytest.importorskip("bpy")

from aigamekit_shared.bpy_mesh import clear_scene, load_glb, save_glb  # noqa: E402
from aigamekit_shared.mesh_simplify import (  # noqa: E402
    _boundary_edge_fraction,
    _nonmanifold_edge_count,
    _weld_if_split_soup,
    decimate_mesh_object,
    has_shape_keys,
    simplify_glb,
    simplify_mesh_object,
)


def _make_dense_sphere(name: str = "Sphere", subdivisions: int = 4) -> object:
    """Ico sphere with ~5120 triangular faces at subdivisions=4."""
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=subdivisions, radius=1.0)
    obj = bpy.context.active_object
    obj.name = name
    return obj


def _split_all_vertices(obj: object) -> None:
    """Parte todos os vértices por canto (V/Tri=3) — como um GLB reimportado."""
    import bmesh

    me = obj.data  # type: ignore[attr-defined]
    bm = bmesh.new()
    bm.from_mesh(me)
    bmesh.ops.split_edges(bm, edges=bm.edges[:])
    bm.to_mesh(me)
    me.update()
    bm.free()


class TestDecimateMeshObject:
    def setup_method(self) -> None:
        clear_scene()

    def teardown_method(self) -> None:
        clear_scene()

    def test_hits_target(self) -> None:
        obj = _make_dense_sphere()
        n0 = len(obj.data.polygons)
        assert n0 > 1000
        result = decimate_mesh_object(obj, 500)
        assert result <= 500
        assert result == len(obj.data.polygons)

    def test_noop_when_already_below_target(self) -> None:
        obj = _make_dense_sphere(subdivisions=1)
        n0 = len(obj.data.polygons)
        assert decimate_mesh_object(obj, n0 + 100) == n0

    def test_shape_keys_skip_without_error(self) -> None:
        obj = _make_dense_sphere()
        obj.shape_key_add(name="Basis")
        obj.shape_key_add(name="Morph")
        assert has_shape_keys(obj)
        n0 = len(obj.data.polygons)
        assert decimate_mesh_object(obj, 200) == n0

    def test_preserves_vertex_groups(self) -> None:
        obj = _make_dense_sphere()
        vg = obj.vertex_groups.new(name="weights")
        vg.add(list(range(len(obj.data.vertices))), 0.7, "REPLACE")
        decimate_mesh_object(obj, 500)
        assert "weights" in obj.vertex_groups

    def test_decimate_moved_before_armature_modifier(self) -> None:
        obj = _make_dense_sphere()
        bpy.ops.object.armature_add(location=(0, 0, 0))
        arm = bpy.context.active_object
        mod = obj.modifiers.new("Armature", "ARMATURE")
        mod.object = arm
        decimate_mesh_object(obj, 500)
        # Armature modifier must survive (never frozen by the apply).
        assert [m.type for m in obj.modifiers] == ["ARMATURE"]
        assert len(obj.data.polygons) <= 500

    def test_fragmented_shells_weld_before_collapse(self) -> None:
        """Cascas coincidentes: o weld funde-as numa só antes do COLLAPSE."""
        obj = _make_double_open_shell()
        n_faces0 = len(obj.data.polygons)
        bfrac0 = _boundary_edge_fraction(obj)
        assert bfrac0 >= 0.12
        welded = _weld_if_split_soup(obj)
        assert welded > 0
        # As duas cascas passam a ser uma: metade das faces/vértices, e sem
        # arestas non-manifold (o weld só-de-fronteira antigo deixava 37).
        assert len(obj.data.polygons) <= n_faces0 // 2
        assert _nonmanifold_edge_count(obj) == 0
        target = max(300, len(obj.data.polygons) // 4)
        n = decimate_mesh_object(obj, target, protect_boundaries=False)
        assert n <= int(target * 1.5)
        # LOD não pode sair "comido" (quase só boundary).
        assert _boundary_edge_fraction(obj) < 0.45

    def test_uv_seam_splits_welded_before_collapse(self) -> None:
        """Costuras do glTF (verts coincidentes) não podem rasgar o LOD.

        Reproduz o import de GLB: cada face com os seus próprios vértices
        (triangle soup). Sem o weld, o COLLAPSE decima cada triângulo isolado
        e o LOD sai em centenas de ilhas — o defeito das árvores lod2.
        """
        obj = _make_dense_sphere(subdivisions=4)
        n_faces0 = len(obj.data.polygons)
        _split_all_vertices(obj)
        assert _boundary_edge_fraction(obj) == pytest.approx(1.0)
        target = n_faces0 // 8
        decimate_mesh_object(obj, target, protect_boundaries=False)
        assert len(obj.data.polygons) <= int(target * 1.2)
        # Reconectada: superfície fechada, não uma sopa de retalhos.
        assert _boundary_edge_fraction(obj) < 0.05


def _make_double_open_shell() -> object:
    """Duas cascas abertas coincidentes (simula paredes duplas Hunyuan)."""
    import bmesh

    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=3, radius=1.0)
    obj = bpy.context.active_object
    assert obj is not None
    # Apaga ~20% faces via bmesh (buracos → boundary).
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    to_del = [f for i, f in enumerate(bm.faces) if i % 5 == 0]
    bmesh.ops.delete(bm, geom=to_del, context="FACES")
    bm.to_mesh(obj.data)
    obj.data.update()
    bm.free()
    # Segunda casca idêntica (verts coincidentes).
    bpy.ops.object.duplicate()
    dup = bpy.context.active_object
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    dup.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.join()
    return obj


class TestSimplifyMeshObject:
    def setup_method(self) -> None:
        clear_scene()

    def teardown_method(self) -> None:
        clear_scene()

    def test_stats_keys(self) -> None:
        obj = _make_dense_sphere()
        stats = simplify_mesh_object(obj, 500, repair=False)
        assert stats["faces_before"] > stats["faces_after"]
        assert stats["faces_after"] <= 500
        assert stats["verts_before"] > 0
        assert stats["verts_after"] > 0


class TestSimplifyGlb:
    def setup_method(self) -> None:
        clear_scene()

    def teardown_method(self) -> None:
        clear_scene()

    def test_multi_mesh_all_parts_exported(self, tmp_path: Path) -> None:
        a = _make_dense_sphere("PartA")
        a.location = (-2, 0, 0)
        b = _make_dense_sphere("PartB")
        b.location = (2, 0, 0)
        src = tmp_path / "multi.glb"
        save_glb([a, b], src)

        dst = tmp_path / "multi_simplified.glb"
        simplify_glb(src, dst, target_faces=800, repair=False)

        objs = load_glb(dst)
        assert len(objs) == 2
        total = sum(len(o.data.polygons) for o in objs)
        assert total <= 800 * 1.3

    def test_armature_preserved(self, tmp_path: Path) -> None:
        obj = _make_dense_sphere("Skinned")
        bpy.ops.object.armature_add(location=(0, 0, 0))
        arm = bpy.context.active_object
        mod = obj.modifiers.new("Armature", "ARMATURE")
        mod.object = arm
        vg = obj.vertex_groups.new(name="Bone")
        vg.add(list(range(len(obj.data.vertices))), 1.0, "REPLACE")

        src = tmp_path / "rigged.glb"
        save_glb([obj, arm], src)

        dst = tmp_path / "rigged_simplified.glb"
        simplify_glb(src, dst, target_faces=500, repair=False)

        load_glb(dst)
        arms = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
        assert len(arms) == 1

    def test_empty_glb_raises(self, tmp_path: Path) -> None:
        clear_scene()
        src = tmp_path / "empty.glb"
        save_glb(None, src)
        with pytest.raises(ValueError, match="Mesh vazia"):
            simplify_glb(src, tmp_path / "out.glb", target_faces=100)
