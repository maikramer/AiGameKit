"""Testes para o reparo pré-decimação em remesh_textured_glb (rachaduras LOD)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("bpy")

import bpy
import numpy as np

from aigamekit_shared.bpy_mesh import clear_scene, create_mesh_from_arrays, save_glb
from text3d.utils.mesh_remesh_textured import remesh_textured_glb


def _boundary_and_sliver_stats(path: Path) -> tuple[int, float]:
    """Devolve (arestas de fronteira pós-weld, aresta máxima)."""
    clear_scene()
    bpy.ops.import_scene.gltf(filepath=str(path))
    obj = max(
        (o for o in bpy.context.scene.objects if o.type == "MESH"),
        key=lambda o: len(o.data.polygons),
    )
    me = obj.data
    nv = len(me.vertices)
    verts = np.empty(nv * 3)
    me.vertices.foreach_get("co", verts)
    verts = verts.reshape(nv, 3)
    me.calc_loop_triangles()
    nt = len(me.loop_triangles)
    faces = np.empty(nt * 3, dtype=np.int64)
    me.loop_triangles.foreach_get("vertices", faces)
    faces = faces.reshape(nt, 3)

    _, remap = np.unique(np.round(verts, 5), axis=0, return_inverse=True)
    wfaces = remap[faces]
    ok = (wfaces[:, 0] != wfaces[:, 1]) & (wfaces[:, 1] != wfaces[:, 2]) & (wfaces[:, 0] != wfaces[:, 2])
    wfaces = wfaces[ok]
    edges = np.sort(wfaces[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2), axis=1)
    _uniq, counts = np.unique(edges, axis=0, return_counts=True)
    n_boundary = int((counts == 1).sum())
    elen = np.linalg.norm(verts[faces[:, [0, 1, 2]]] - verts[faces[:, [1, 2, 0]]], axis=2)
    return n_boundary, float(elen.max()) if len(elen) else 0.0


def _save_sphere_with_sliver(path: Path) -> Path:
    """Icosfera + triângulo-leque gigante (sliver de decode corrompido)."""
    clear_scene()
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=3)
    obj = bpy.context.active_object

    me = obj.data
    nv = len(me.vertices)
    verts = np.empty(nv * 3)
    me.vertices.foreach_get("co", verts)
    verts = verts.reshape(nv, 3)
    me.calc_loop_triangles()
    nt = len(me.loop_triangles)
    faces = np.empty(nt * 3, dtype=np.int64)
    me.loop_triangles.foreach_get("vertices", faces)
    faces = faces.reshape(nt, 3)

    # leque: liga dois pontos opostos da esfera a um vértice remoto
    sliver_verts = np.array([[10.0, 0.0, 0.0]])
    verts2 = np.vstack([verts, sliver_verts])
    sliver_face = np.array([[0, nv // 2, nv]], dtype=np.int64)
    faces2 = np.vstack([faces, sliver_face])

    clear_scene()
    obj = create_mesh_from_arrays(verts2, faces2)
    # UV layer mínimo para o caminho texturado não rejeitar
    obj.data.uv_layers.new(name="UVMap")
    save_glb([obj], path)
    return path


class TestPreDecimateRepair:
    def test_repair_removes_sliver_fan(self, tmp_path: Path) -> None:
        inp = _save_sphere_with_sliver(tmp_path / "sliver.glb")
        _, max_edge_before = _boundary_and_sliver_stats(inp)
        assert max_edge_before > 5.0  # leque presente no input

        out = tmp_path / "decimated.glb"
        remesh_textured_glb(inp, out, target_faces=300, texture_size=64)
        _, max_edge_after = _boundary_and_sliver_stats(out)
        assert max_edge_after < 5.0  # leque removido pelo reparo

    def test_repair_flag_off_keeps_raw_path(self, tmp_path: Path) -> None:
        inp = _save_sphere_with_sliver(tmp_path / "sliver.glb")
        out = tmp_path / "raw.glb"
        remesh_textured_glb(inp, out, target_faces=300, texture_size=64, repair=False)
        assert out.is_file()

    def test_decimated_sphere_stays_closed(self, tmp_path: Path) -> None:
        clear_scene()
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=4)
        obj = bpy.context.active_object
        obj.data.uv_layers.new(name="UVMap")
        inp = tmp_path / "sphere.glb"
        save_glb([obj], inp)

        out = tmp_path / "sphere_lod.glb"
        remesh_textured_glb(inp, out, target_faces=500, texture_size=64)
        n_boundary, _ = _boundary_and_sliver_stats(out)
        assert n_boundary == 0  # esfera fechada não pode ganhar rachaduras


class TestClampDecimateTarget:
    def test_raises_floor_on_extreme_ratio(self) -> None:
        from text3d.utils.mesh_remesh_textured import _clamp_decimate_target

        # Piso relativo é só rede de segurança (0.05%): 246k → 123.
        # O absoluto (150) domina, e um pedido de 750 passa intacto — o piso
        # antigo (0.8% ≈ 1968) substituía o orçamento de LOD e achatava a ladder.
        assert _clamp_decimate_target(246_013, 750) == 750
        assert _clamp_decimate_target(246_013, 10) == 150

    def test_keeps_request_when_above_floor(self) -> None:
        from text3d.utils.mesh_remesh_textured import _clamp_decimate_target

        assert _clamp_decimate_target(100_000, 10_000) == 10_000

    def test_noop_when_target_ge_source(self) -> None:
        from text3d.utils.mesh_remesh_textured import _clamp_decimate_target

        assert _clamp_decimate_target(1000, 2000) == 2000
