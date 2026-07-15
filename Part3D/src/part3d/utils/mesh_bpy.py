"""Mesh repair via bpy (estilo Text3D) — sem pymeshlab.

Substitui ``partgen.utils.mesh_utils.fix_mesh`` (PyMeshLab: remove floaters
+ edges longas) por topologia bpy: remove doubles, debris, fill holes, normals.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from gamedev_shared.bpy_mesh import clear_scene, create_mesh_from_arrays
from gamedev_shared.logging import Logger

_log = Logger()


def _remove_doubles(obj: Any, threshold: float) -> int:
    import bpy

    before = len(obj.data.vertices)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.remove_doubles(threshold=threshold, use_sharp_edge_from_normals=False)
    bpy.ops.object.mode_set(mode="OBJECT")
    return before - len(obj.data.vertices)


def _fill_holes(obj: Any, sides: int = 12) -> None:
    import bpy

    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="DESELECT")
    bpy.ops.mesh.select_non_manifold()
    bpy.ops.mesh.fill_holes(sides=sides)
    bpy.ops.object.mode_set(mode="OBJECT")


def _triangulate(obj: Any) -> None:
    import bpy

    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.quads_convert_to_tris(quad_method="BEAUTY", ngon_method="BEAUTY")
    bpy.ops.object.mode_set(mode="OBJECT")


def _normals_consistent(obj: Any) -> None:
    import bpy

    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.normals_make_consistent()
    bpy.ops.object.mode_set(mode="OBJECT")


def _remove_loose_debris(obj: Any, *, face_ratio: float = 0.1, min_faces: int = 8) -> int:
    """Apaga ilhas minúsculas (floaters) — equivalente a ``remove_floater`` PyMeshLab.

    Componentes com menos de ``max(min_faces, face_ratio * total_faces)`` faces
    são removidos. Nunca apaga a maior ilha.
    """
    if face_ratio <= 0:
        return 0

    import bmesh

    me = obj.data
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.faces.ensure_lookup_table()
    bm.verts.ensure_lookup_table()

    parent = list(range(len(bm.verts)))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for e in bm.edges:
        ra, rb = find(e.verts[0].index), find(e.verts[1].index)
        if ra != rb:
            parent[rb] = ra

    faces_per_comp: dict[int, int] = {}
    face_root: list[int] = []
    for f in bm.faces:
        r = find(f.verts[0].index)
        face_root.append(r)
        faces_per_comp[r] = faces_per_comp.get(r, 0) + 1

    if not faces_per_comp:
        bm.free()
        return 0

    total_faces = len(bm.faces)
    threshold = max(min_faces, int(face_ratio * total_faces))
    largest = max(faces_per_comp.values())
    doomed_roots = {r for r, n in faces_per_comp.items() if n < threshold and n < largest}
    if not doomed_roots:
        bm.free()
        return 0

    doomed_faces = [f for f, r in zip(bm.faces, face_root, strict=True) if r in doomed_roots]
    removed = len(doomed_faces)
    doomed_verts = [v for v in bm.verts if find(v.index) in doomed_roots]
    bmesh.ops.delete(bm, geom=doomed_faces, context="FACES")
    bmesh.ops.delete(bm, geom=[v for v in doomed_verts if v.is_valid], context="VERTS")
    bm.to_mesh(me)
    me.update()
    bm.free()
    return removed


def _remove_long_edges(obj: Any, max_length: float) -> int:
    """Remove faces com aresta outlier (equiv. ``remove_overlength_edge``).

    Limiar efectivo = ``max(max_length, 8 * mediana)`` para não destruir meshes
    grossas (ex. caixa unitária) enquanto ainda corta triângulos MC degenerados.
    Se a selecção apagaria >50% das faces, aborta (conservador).
    """
    import bmesh

    me = obj.data
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.faces.ensure_lookup_table()
    bm.edges.ensure_lookup_table()

    if not bm.edges:
        bm.free()
        return 0

    lengths = [e.calc_length() for e in bm.edges]
    median = float(sorted(lengths)[len(lengths) // 2])
    threshold = max(float(max_length), 8.0 * median)

    doomed = [f for f in bm.faces if any(e.calc_length() > threshold for e in f.edges)]
    if not doomed or len(doomed) > 0.5 * len(bm.faces):
        bm.free()
        return 0

    n = len(doomed)
    bmesh.ops.delete(bm, geom=doomed, context="FACES")
    orphans = [v for v in bm.verts if not v.link_faces]
    if orphans:
        bmesh.ops.delete(bm, geom=orphans, context="VERTS")
    bm.to_mesh(me)
    me.update()
    bm.free()
    return n


def _mesh_to_trimesh(obj: Any) -> Any:
    import trimesh

    me = obj.data
    verts = np.array([tuple(v.co) for v in me.vertices], dtype=np.float64)
    faces = np.array([tuple(p.vertices) for p in me.polygons], dtype=np.int64)
    if verts.size == 0 or faces.size == 0:
        return trimesh.Trimesh(vertices=np.zeros((0, 3)), faces=np.zeros((0, 3), dtype=np.int64), process=False)
    return trimesh.Trimesh(vertices=verts, faces=faces, process=False)


def _boundary_loops(bm: Any) -> list[list[Any]]:
    """Agrupa arestas de fronteira (1 face) em loops fechados."""
    boundary = [e for e in bm.edges if len(e.link_faces) == 1]
    unvisited = set(boundary)
    loops: list[list[Any]] = []
    while unvisited:
        start = unvisited.pop()
        loop = [start]
        # caminhar pelas arestas de fronteira ligadas por vértices
        cur = start
        v = cur.verts[1]
        while True:
            nxt = None
            for e in v.link_edges:
                if e in unvisited:
                    nxt = e
                    break
            if nxt is None:
                break
            unvisited.discard(nxt)
            loop.append(nxt)
            v = nxt.other_vert(v)
        loops.append(loop)
    return loops


def _loop_is_cappable(loop: list[Any], *, max_edges: int, planar_tol: float) -> bool:
    """Loop pequeno e ~planar → seguro tapar. Cortes gigantes/serpenteantes → não."""
    if len(loop) < 3 or len(loop) > max_edges:
        return False
    pts = np.array([v.co[:] for e in loop for v in e.verts], dtype=np.float64)
    center = pts.mean(axis=0)
    q = pts - center
    # menor valor singular = espessura fora do plano
    sv = np.linalg.svd(q, compute_uv=False)
    diameter = float(np.linalg.norm(pts.max(axis=0) - pts.min(axis=0)))
    if diameter <= 0:
        return False
    thickness = float(sv[-1]) / np.sqrt(max(len(pts), 1))
    return thickness < planar_tol * diameter


def cap_boundary_holes(mesh: Any, *, max_loop_edges: int = 400, planar_tol: float = 0.10) -> Any:
    """Fecha buracos de fronteira de uma parte extraída — só loops seguros.

    Usado pelo face-split: uma parte cortada da mesh original (ex. porta)
    deixa loops abertos no corte. Tapar TODOS os loops (``fill_holes(sides=0)``)
    cria membranas gigantes sobre cortes não-planares (fachada inteira tapada
    por um "sólido"). Aqui só se tapam loops com ≤ ``max_loop_edges`` arestas
    e aproximadamente planares (desvio RMS < ``planar_tol`` x diâmetro).

    Args:
        mesh: Objecto com ``.vertices`` / ``.faces`` (ex. ``trimesh.Trimesh``).
        max_loop_edges: Máximo de arestas de um loop tapável.
        planar_tol: Tolerância de planaridade relativa ao diâmetro do loop.

    Returns:
        ``trimesh.Trimesh`` com os loops seguros fechados (input se falha).
    """
    verts = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    if verts.size == 0 or faces.size == 0:
        return mesh

    import bmesh
    import bpy

    clear_scene()
    obj = create_mesh_from_arrays(verts, faces, name="part3d_cap")
    try:
        _remove_doubles(obj, threshold=1e-6)

        bpy.context.view_layer.objects.active = obj
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bm.edges.ensure_lookup_table()
        capped = 0
        for loop in _boundary_loops(bm):
            if not _loop_is_cappable(loop, max_edges=max_loop_edges, planar_tol=planar_tol):
                continue
            edges = [e for e in loop if e.is_valid]
            if len(edges) < 3:
                continue
            try:
                bmesh.ops.holes_fill(bm, edges=edges, sides=0)
                capped += 1
            except Exception:
                continue
        if capped:
            bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
        bm.to_mesh(obj.data)
        obj.data.update()
        bm.free()

        if capped:
            _normals_consistent(obj)
            _log.dim(f"cap_boundary_holes: {capped} loops fechados")
        out = _mesh_to_trimesh(obj)
        return out if len(out.faces) > 0 else mesh
    except Exception as exc:
        _log.warn(f"cap_boundary_holes bpy falhou ({exc}); a devolver mesh original")
        return mesh
    finally:
        clear_scene()


def fix_mesh(mesh: Any, *, max_edge_length: float = 8.0 / 512.0, fill_holes_sides: int = 12) -> Any:
    """Repara mesh pós-decode (marching cubes) com bpy.

    Espelha o contrato de ``partgen.utils.mesh_utils.fix_mesh`` (entrada/saída
    trimesh-like com ``.vertices`` / ``.faces``), sem PyMeshLab.

    Args:
        mesh: Objecto com ``.vertices`` e ``.faces`` (ex. ``trimesh.Trimesh``).
        max_edge_length: Limiar para remover faces com arestas longas (XPart default).
        fill_holes_sides: Max lados para ``fill_holes`` bpy.

    Returns:
        ``trimesh.Trimesh`` reparado (ou o input se vazio / falha não-crítica).
    """
    verts = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    if verts.size == 0 or faces.size == 0:
        return mesh

    # Verts NaN/Inf passam invisíveis pelas comparações (NaN > x = False) e o
    # exporter glTF converte-os em (0,0,0) — leque de faces na origem. Dropar
    # faces com vértices não-finitos antes de qualquer reparação.
    finite = np.isfinite(verts).all(axis=1)
    if not finite.all():
        bad = int((~finite).sum())
        keep_faces = finite[faces].all(axis=1)
        faces = faces[keep_faces]
        _log.warn(f"fix_mesh: {bad} vértices não-finitos; {int((~keep_faces).sum())} faces removidas")
        if faces.size == 0:
            return mesh

    clear_scene()
    obj = create_mesh_from_arrays(verts, faces, name="part3d_fix")

    try:
        _remove_doubles(obj, threshold=1e-5)
        bbox = obj.dimensions
        diag = float(np.linalg.norm([bbox.x, bbox.y, bbox.z])) or 1.0
        _remove_doubles(obj, threshold=max(1e-4, 0.0001 * diag))
        n_long = _remove_long_edges(obj, max_length=max_edge_length)
        n_debris = _remove_loose_debris(obj)
        try:
            _normals_consistent(obj)
        except Exception as exc:
            _log.warn(f"normals_make_consistent falhou: {exc}")
        if fill_holes_sides > 0:
            try:
                _fill_holes(obj, sides=fill_holes_sides)
                _triangulate(obj)  # fill_holes cria n-gons; _mesh_to_trimesh exige triângulos
            except Exception as exc:
                _log.warn(f"fill_holes falhou: {exc}")
        if n_long or n_debris:
            _log.dim(f"fix_mesh bpy: long_edges={n_long} debris_faces={n_debris}")
        return _mesh_to_trimesh(obj)
    except Exception as exc:
        _log.warn(f"fix_mesh bpy falhou ({exc}); a devolver mesh original")
        return mesh
    finally:
        clear_scene()
