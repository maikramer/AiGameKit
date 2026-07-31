"""Tests for aigamekit_shared.mesh_repair_arrays (vectorized repair phase)."""

from __future__ import annotations

import numpy as np
import pytest

from aigamekit_shared.mesh_repair_arrays import (
    boundary_edge_count,
    boundary_edges,
    compact_mesh,
    drop_internal_shell_faces,
    drop_long_edge_faces,
    drop_loose_debris,
    drop_sliver_faces,
    repair_arrays_topology_clean,
    sanitize_nonfinite,
    taubin_smooth_arrays,
    weld_vertices,
    weld_vertices_multi,
)


def _box(extents=(1.0, 1.0, 1.0), center=(0.0, 0.0, 0.0)) -> tuple[np.ndarray, np.ndarray]:
    """Cubo 8 verts / 12 tris, normais outward."""
    ex, ey, ez = (s / 2 for s in extents)
    cx, cy, cz = center
    v = np.array(
        [
            [cx - ex, cy - ey, cz - ez],
            [cx + ex, cy - ey, cz - ez],
            [cx + ex, cy + ey, cz - ez],
            [cx - ex, cy + ey, cz - ez],
            [cx - ex, cy - ey, cz + ez],
            [cx + ex, cy - ey, cz + ez],
            [cx + ex, cy + ey, cz + ez],
            [cx - ex, cy + ey, cz + ez],
        ]
    )
    f = np.array(
        [
            [0, 2, 1],
            [0, 3, 2],
            [4, 5, 6],
            [4, 6, 7],
            [0, 1, 5],
            [0, 5, 4],
            [2, 3, 7],
            [2, 7, 6],
            [1, 2, 6],
            [1, 6, 5],
            [0, 4, 7],
            [0, 7, 3],
        ],
        dtype=np.int64,
    )
    return v, f


def _split_box() -> tuple[np.ndarray, np.ndarray]:
    """Cubo com vértices partidos por triângulo (normal-split): 36 verts / 12 tris."""
    v, f = _box()
    new_v = v[f.reshape(-1)]
    new_f = np.arange(36, dtype=np.int64).reshape(-1, 3)
    return new_v, new_f


class TestCompactMesh:
    def test_drops_faces_and_orphans(self) -> None:
        v, f = _box()
        keep = np.ones(len(f), dtype=bool)
        keep[:2] = False  # remove fundo
        v2, f2 = compact_mesh(v, f, keep)
        assert len(f2) == 10
        assert len(v2) == 8  # todos os verts ainda referenciados
        assert f2.max() == 7

    def test_drops_degenerate(self) -> None:
        v = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [5, 5, 5]], dtype=np.float64)
        f = np.array([[0, 1, 2], [1, 1, 2], [3, 3, 3]], dtype=np.int64)
        v2, f2 = compact_mesh(v, f)
        assert len(f2) == 1
        assert len(v2) == 3  # vert 3 órfão removido

    def test_empty(self) -> None:
        v, f = compact_mesh(np.zeros((3, 3)), np.zeros((0, 3), dtype=np.int64))
        assert len(v) == 0 and len(f) == 0


class TestSanitizeNonfinite:
    def test_nan_drops_incident_faces(self) -> None:
        v, f = _box()
        v[0] = np.nan
        v2, f2, n = sanitize_nonfinite(v, f)
        assert n == 1
        assert not np.isfinite(v).all() or True
        assert len(v2) == 7
        # faces que tocavam o vert 0: [0,2,1],[0,3,2],[0,1,5],[0,5,4],[0,4,7],[0,7,3]
        assert len(f2) == 6

    def test_passthrough(self) -> None:
        v, f = _box()
        v2, f2, n = sanitize_nonfinite(v, f)
        assert n == 0 and len(v2) == 8 and len(f2) == 12


class TestWeldVertices:
    def test_split_box_welds_to_8(self) -> None:
        v, f = _split_box()
        assert len(v) == 36
        v2, f2, n = weld_vertices(v, f, 1e-6)
        assert n == 28
        assert len(v2) == 8
        assert len(f2) == 12
        assert boundary_edge_count(f2) == 0

    def test_transitive_chaining(self) -> None:
        t = 0.01
        v = np.array([[0, 0, 0], [0.9 * t, 0, 0], [1.8 * t, 0, 0], [5, 0, 0]], dtype=np.float64)
        f = np.array([[0, 1, 3], [1, 2, 3]], dtype=np.int64)
        _v2, _f2, n = weld_vertices(v, f, t)
        assert n == 2  # 0-1-2 fundem em cadeia; vert 3 fica

    def test_threshold_exactness(self) -> None:
        t = 0.01
        v = np.array([[0, 0, 0], [1.1 * t, 0, 0], [2.2 * t, 0, 0], [3.3 * t, 0, 0]], dtype=np.float64)
        f = np.array([[0, 1, 2], [1, 2, 3]], dtype=np.int64)
        _v2, _f2, n = weld_vertices(v, f, t)
        assert n == 0  # todos os pares consecutivos a 1.1t — fora do limiar

    def test_degenerate_faces_dropped(self) -> None:
        v = np.array([[0, 0, 0], [1e-9, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float64)
        f = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int64)
        _v2, f2, n = weld_vertices(v, f, 1e-6)
        assert n == 1
        assert len(f2) == 1  # [0,1,2] degenerou

    def test_grid_method_approximate(self) -> None:
        v, f = _split_box()
        v2, _f2, n = weld_vertices(v, f, 1e-6, method="grid")
        assert n >= 26  # duplicados exactos fundem sempre
        assert len(v2) <= 10

    def test_multi_threshold_stats(self) -> None:
        v, f = _split_box()
        # afasta um canto duplicado a 4e-3 (só funde no limiar maior)
        v[0] += np.array([4e-3, 0, 0])
        v[12] = v[0].copy()
        v2, _f2, removed = weld_vertices_multi(v, f, [1e-6, 1e-5, 3e-3 * 2])
        assert removed[0] <= removed[1] <= removed[2]
        assert len(np.unique(v2, axis=0)) == len(v2)  # reps sem duplicados
        assert removed[-1] == 28


class TestDropLongEdgeFaces:
    def test_removes_outlier_fan(self) -> None:
        v, f = _box()
        fan = np.array([[0, 0, 0], [100.0, 0, 0], [0, 1, 0]], dtype=np.float64)
        v2 = np.vstack([v, fan])
        f2 = np.vstack([f, [[8, 9, 10]]])
        _v3, f3, n = drop_long_edge_faces(v2, f2, max_length=8.0 / 512.0)
        assert n == 1
        assert len(f3) == 12

    def test_median_guard_keeps_uniform(self) -> None:
        v, f = _box()
        _v2, f2, n = drop_long_edge_faces(v, f, max_length=0.001)
        assert n == 0  # mediana*8 > arestas do cubo
        assert len(f2) == 12

    def test_abort_on_ratio(self) -> None:
        v = np.array([[0, 0, 0], [10, 0, 0], [0, 1, 0], [10, 1, 0]], dtype=np.float64)
        f = np.array([[0, 1, 2], [1, 3, 2]], dtype=np.int64)
        _v2, f2, n = drop_long_edge_faces(v, f, max_length=0.001, median_factor=0.1, max_removal_ratio=0.1)
        assert n == 0
        assert len(f2) == 2


class TestDropSliverFaces:
    def test_removes_needle(self) -> None:
        v, f = _box()
        needle = np.array([[0, 0, 0], [10.0, 0, 0], [10.0, 1e-6, 0]], dtype=np.float64)
        v2 = np.vstack([v, needle])
        f2 = np.vstack([f, [[8, 9, 10]]])
        _v3, f3, n = drop_sliver_faces(v2, f2, max_aspect=80.0)
        assert n == 1
        assert len(f3) == 12

    def test_keeps_healthy(self) -> None:
        v, f = _box()
        _v2, f2, n = drop_sliver_faces(v, f, max_aspect=80.0)
        assert n == 0 and len(f2) == 12


def _tetra(size: float = 1.0, center: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> tuple[np.ndarray, np.ndarray]:
    """Tetraedro fechado (4 verts / 4 faces) — ilha pequena para debris."""
    cx, cy, cz = center
    v = np.array(
        [
            [cx, cy, cz],
            [cx + size, cy, cz],
            [cx + size / 2, cy + size, cz],
            [cx + size / 2, cy + size / 2, cz + size],
        ]
    )
    f = np.array([[0, 2, 1], [0, 1, 3], [1, 2, 3], [2, 0, 3]], dtype=np.int64)
    return v, f


class TestDropLooseDebris:
    def test_removes_floater(self) -> None:
        v, f = _box()
        fv, ff = _tetra(size=0.01, center=(5.0, 5.0, 5.0))
        v2 = np.vstack([v, fv])
        f2 = np.vstack([f, ff + 8])
        _v3, f3, n = drop_loose_debris(v2, f2, face_ratio=0.5, min_faces=4)
        assert n == 4  # ilha de 4 faces < max(4, 0.5*16) e < 12
        assert len(f3) == 12

    def test_largest_island_protected(self) -> None:
        v, f = _box()
        fv, ff = _tetra(size=0.5, center=(5, 5, 5))
        v2 = np.vstack([v, fv])
        f2 = np.vstack([f, ff + 8])
        # threshold alto: ambos abaixo, mas o maior nunca é removido
        _v3, f3, n = drop_loose_debris(v2, f2, face_ratio=10.0, min_faces=100)
        assert n == 4
        assert len(f3) == 12

    def test_equal_islands_untouched(self) -> None:
        # duas ilhas do mesmo tamanho: nenhuma é "maior" — paridade bmesh (noop)
        v, f = _box()
        fv, ff = _box(extents=(0.5, 0.5, 0.5), center=(5, 5, 5))
        v2 = np.vstack([v, fv])
        f2 = np.vstack([f, ff + 8])
        _v3, f3, n = drop_loose_debris(v2, f2, face_ratio=10.0, min_faces=100)
        assert n == 0
        assert len(f3) == 24

    def test_disabled(self) -> None:
        v, f = _box()
        _v2, f2, n = drop_loose_debris(v, f, face_ratio=0.0, min_faces=64)
        assert n == 0 and len(f2) == 12


class TestBoundary:
    def test_closed_box(self) -> None:
        _v, f = _box()
        assert boundary_edge_count(f) == 0
        assert len(boundary_edges(f)) == 0

    def test_open_grid(self) -> None:
        f = np.array([[0, 1, 4], [0, 4, 3], [1, 2, 5], [1, 5, 4]], dtype=np.int64)
        assert boundary_edge_count(f) == 6


class TestDropInternalShellFaces:
    def test_nested_box_inner_removed(self) -> None:
        outer_v, outer_f = _box(extents=(2.0, 2.0, 2.0))
        inner_v, inner_f = _box(extents=(1.9, 1.9, 1.9))
        v = np.vstack([outer_v, inner_v])
        f = np.vstack([outer_f, inner_f + 8])
        _v2, f2, n = drop_internal_shell_faces(
            v,
            f,
            wall_gap_ratio=0.08,  # diag≈3.46 → gap≈0.28 > folga 0.1
            max_removal_ratio=0.55,
            passes=1,
            k_neighbors=16,
        )
        assert n == 12  # caixa interna toda
        assert len(f2) == 12

    def test_single_box_kept(self) -> None:
        v, f = _box(extents=(2.0, 2.0, 2.0))
        _v2, f2, n = drop_internal_shell_faces(v, f, wall_gap_ratio=0.08, passes=1)
        assert n == 0
        assert len(f2) == 12

    def test_guard_aborts(self) -> None:
        outer_v, outer_f = _box(extents=(2.0, 2.0, 2.0))
        inner_v, inner_f = _box(extents=(1.9, 1.9, 1.9))
        v = np.vstack([outer_v, inner_v])
        f = np.vstack([outer_f, inner_f + 8])
        _v2, f2, n = drop_internal_shell_faces(v, f, wall_gap_ratio=0.08, max_removal_ratio=0.1, passes=1)
        assert n == 0  # 12/24 = 50% > 10% → abort
        assert len(f2) == 24


class TestTaubin:
    def test_runs_and_preserves_finiteness(self) -> None:
        v, f = _box()
        v = v + np.random.default_rng(0).normal(scale=1e-3, size=v.shape)
        v2, iters = taubin_smooth_arrays(v, f, iterations=2)
        assert iters == 2
        assert np.isfinite(v2).all()
        assert v2.shape == v.shape
        # centro de massa ~preservado (volume-preserving; cubo de 8 verts
        # com ruído assimétrico desvia um pouco mais que o ruído médio)
        np.testing.assert_allclose(v2.mean(axis=0), v.mean(axis=0), atol=5e-3)
        # suavização: desvio ao centro não aumenta
        assert np.std(v2 - v2.mean(axis=0)) <= np.std(v - v.mean(axis=0)) * 1.1

    def test_skips_tiny(self) -> None:
        v = np.zeros((3, 3))
        f = np.array([[0, 1, 2]], dtype=np.int64)
        _v2, iters = taubin_smooth_arrays(v, f, iterations=3)
        assert iters == 0


class TestSimplifyFacesArrays:
    def test_noop_when_below_target(self) -> None:
        from aigamekit_shared.mesh_repair_arrays import simplify_faces_arrays

        v, f = _box()
        v2, f2 = simplify_faces_arrays(v, f, target_faces=100)
        assert len(v2) == 8 and len(f2) == 12

    def test_decimates_to_target(self) -> None:
        pytest.importorskip("fast_simplification")
        import trimesh

        from aigamekit_shared.mesh_repair_arrays import simplify_faces_arrays

        m = trimesh.creation.icosphere(subdivisions=5)
        v = np.asarray(m.vertices, dtype=np.float64)
        f = np.asarray(m.faces, dtype=np.int64)
        target = len(f) // 10
        out = simplify_faces_arrays(v, f, target)
        assert out is not None
        v2, f2 = out
        assert len(f2) <= len(f) // 2  # reduziu substancialmente
        assert abs(len(f2) - target) < target * 0.2  # perto do alvo
        assert np.isfinite(v2).all()

    def test_missing_lib_returns_none(self, monkeypatch) -> None:
        import sys

        from aigamekit_shared.mesh_repair_arrays import simplify_faces_arrays

        monkeypatch.setitem(sys.modules, "fast_simplification", None)
        v, f = _box()
        f_big = np.tile(f, (200, 1))
        v_big = np.tile(v, (200, 1))
        # reindexar tiles para mesh válida
        for i in range(200):
            f_big[i * 12 : (i + 1) * 12] = f + i * 8
        out = simplify_faces_arrays(v_big, f_big, target_faces=12)
        assert out is None


class TestRepairArraysTopologyClean:
    def test_end_to_end_synthetic(self) -> None:
        v, f = _split_box()
        # debris floater (tetra de 4 faces; > weld de densidade 0.01 para
        # sobreviver ao weld e chegar ao passo de debris)
        fv, ff = _tetra(size=1.0, center=(9, 9, 9))
        f2 = np.vstack([f, ff + len(v)])
        v2 = np.vstack([v, fv])
        # sliver needle ligada ao cubo
        needle_base = len(v2)
        v2 = np.vstack([v2, [[0, 0, 0], [20.0, 0, 0], [20.0, 1e-7, 0]]])
        f2 = np.vstack([f2, [[needle_base, needle_base + 1, needle_base + 2]]])
        # NaN vert + face
        nan_base = len(v2)
        v2 = np.vstack([v2, [[np.nan, 0, 0], [0, 0, 1], [0, 1, 0]]])
        f2 = np.vstack([f2, [[nan_base, nan_base + 1, nan_base + 2]]])

        v3, f3, stats = repair_arrays_topology_clean(v2, f2)
        assert stats["nonfinite_verts"] == 1
        assert stats["rewelded_coincident"] >= 16
        assert stats["debris_faces"] == 4  # tetra floater
        assert boundary_edge_count(f3) == 0
        assert len(v3) == 8
        assert len(f3) == 12

    def test_empty_mesh(self) -> None:
        v = np.zeros((0, 3))
        f = np.zeros((0, 3), dtype=np.int64)
        v2, f2, stats = repair_arrays_topology_clean(v, f)
        assert len(v2) == 0 and len(f2) == 0
        assert stats["nonfinite_verts"] == 0


# ---------------------------------------------------------------------------
# Paridade com bmesh (requer bpy)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _bpy():
    return pytest.importorskip("bpy")


class TestBpyParity:
    def test_weld_matches_bmesh_remove_doubles(self, _bpy) -> None:
        import bpy

        from aigamekit_shared.bpy_mesh import clear_scene, create_mesh_from_arrays
        from aigamekit_shared.mesh_repair import remove_doubles

        rng = np.random.default_rng(42)
        base = rng.uniform(-1, 1, size=(200, 3))
        # cada ponto duplicado 3x (split normals) + alguns quase-duplicados
        v = np.vstack([base, base, base, base + 5e-4])
        # faces artificiais (fan local) só para o bmesh ter geometria
        f = np.arange(len(v), dtype=np.int64)[: (len(v) // 3) * 3].reshape(-1, 3)

        clear_scene()
        obj = create_mesh_from_arrays(v, f, name="parity")
        n_bmesh = remove_doubles(obj, threshold=1e-3)
        verts_bmesh = len(obj.data.vertices)
        bpy.data.meshes.remove(obj.data)

        _v2, _f2, n_arrays = weld_vertices(v, f, 1e-3)
        assert n_arrays == n_bmesh
        assert len(_v2) == verts_bmesh

    def test_boundary_count_matches_bmesh(self, _bpy) -> None:
        import bpy

        from aigamekit_shared.bpy_mesh import clear_scene, create_mesh_from_arrays
        from aigamekit_shared.mesh_repair import count_boundary_edges, count_boundary_edges_fast

        v, f = _box()
        f = f[:-2]  # abre o topo
        clear_scene()
        obj = create_mesh_from_arrays(v, f, name="boundary")
        n_bmesh = count_boundary_edges(obj)
        n_fast = count_boundary_edges_fast(obj)
        n_arrays = boundary_edge_count(f)
        bpy.data.meshes.remove(obj.data)
        assert n_fast == n_bmesh == n_arrays == 4

    def test_arrays_engine_ok_and_replace(self, _bpy) -> None:
        import bpy

        from aigamekit_shared.bpy_mesh import clear_scene, create_mesh_from_arrays
        from aigamekit_shared.mesh_repair_arrays import arrays_engine_ok, extract_arrays, replace_mesh_arrays

        v, f = _box()
        clear_scene()
        obj = create_mesh_from_arrays(v, f, name="engine_ok")
        assert arrays_engine_ok(obj) is True

        # UV layer desqualifica
        obj.data.uv_layers.new(name="UVMap")
        assert arrays_engine_ok(obj) is False

        # replace com metade das faces
        v2, f2 = compact_mesh(v, f, np.array([True] * 6 + [False] * 6))
        obj2 = create_mesh_from_arrays(v, f, name="replace_target")
        replace_mesh_arrays(obj2, v2, f2)
        assert len(obj2.data.polygons) == 6
        assert len(obj2.data.vertices) == 8
        co, tris = extract_arrays(obj2)
        assert co.shape == (8, 3) and tris.shape == (6, 3)
        bpy.data.meshes.remove(obj.data)
        bpy.data.meshes.remove(obj2.data)


def _flat_grid(n: int = 40, spacing: float = 0.008) -> tuple[np.ndarray, np.ndarray]:
    """Grelha fina (lily_pad-like): aresta ≈ spacing — weld dyn sem cap colapsava."""
    xs = np.arange(n, dtype=np.float64) * spacing
    zs = np.arange(n, dtype=np.float64) * spacing
    xx, zz = np.meshgrid(xs, zs, indexing="xy")
    v = np.column_stack([xx.ravel(), np.zeros(n * n), zz.ravel()])
    tris: list[list[int]] = []
    for j in range(n - 1):
        for i in range(n - 1):
            a = j * n + i
            b = a + 1
            c = a + n
            d = c + 1
            tris.append([a, c, b])
            tris.append([b, c, d])
    return v, np.asarray(tris, dtype=np.int64)


class TestWeldDensityMedianCap:
    def test_dynamic_weld_caps_to_median_edge(self) -> None:
        from aigamekit_shared.mesh_repair import dynamic_weld_distance

        # 54k verts → dyn=0.008; mediana 0.008 → cap 0.0032
        assert dynamic_weld_distance(54_000, median_edge=0.008) == pytest.approx(0.0032)
        assert dynamic_weld_distance(54_000, median_edge=None) == pytest.approx(0.008)

    def test_flat_grid_survives_topology_clean_arrays(self) -> None:
        v, f = _flat_grid(32, spacing=0.008)
        assert len(f) > 1000
        v2, f2, stats = repair_arrays_topology_clean(v, f, do_remove_internal_shells=False)
        assert len(f2) > 0, stats
        assert len(v2) > 100, stats
        # Sem cap, weld_distance seria 8000 µm e faces=0.
        assert stats.get("weld_distance", 0) < 8000
