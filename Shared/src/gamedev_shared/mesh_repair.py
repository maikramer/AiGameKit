"""Primitivas partilhadas de reparação de mesh via bpy — sem PyMeshLab.

Unifica os helpers de reparação que viviam duplicados em Text3D
(``mesh_lod``/``mesh_remesh_textured``), Part3D (``utils/mesh_bpy``) e
Paint3D (``mesh_io``), na variante mais avançada de cada um:

* ``remove_doubles`` — weld via **bmesh** (sem EDIT mode; estilo Paint3D).
* ``remove_loose_debris`` — union-find de ilhas com preservação garantida da
  maior ilha (estilo Part3D, superset do abort-all do Text3D).
* ``remove_long_edges`` — corte de arestas outlier com limiar mediano.
* ``cap_boundary_holes`` — fecho seletivo de boundary loops (só pequenos e
  ~planares via SVD; loops de corte gigantes ficam abertos).
* ``fix_mesh`` — pipeline pós-marching-cubes com guarda anti-NaN (verts NaN
  passam invisíveis em comparações e viram (0,0,0) no export glTF).

Todas as funções importam ``bpy``/``bmesh`` lazily — o módulo pode ser
importado em ambientes sem Blender.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from gamedev_shared.bpy_mesh import clear_scene, create_mesh_from_arrays

log = logging.getLogger(__name__)

_EPS = 1e-12


# ---------------------------------------------------------------------------
# Primitivas sobre bpy objects
# ---------------------------------------------------------------------------


def remove_doubles(obj: Any, threshold: float) -> int:
    """Funde vértices duplicados dentro de ``threshold`` (bmesh, sem EDIT mode).

    Args:
        obj: bpy mesh object.
        threshold: Distância máxima de weld.

    Returns:
        Número de vértices removidos.
    """
    import bmesh

    me = obj.data
    bm = bmesh.new()
    bm.from_mesh(me)
    before = len(bm.verts)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=threshold)
    bm.to_mesh(me)
    me.update()
    removed = before - len(me.vertices)
    bm.free()
    return removed


def fill_holes(obj: Any, sides: int = 12) -> None:
    """Preenche buracos com até ``sides`` arestas (``0`` = todos; usar com cuidado)."""
    import bpy

    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="DESELECT")
    bpy.ops.mesh.select_non_manifold()
    bpy.ops.mesh.fill_holes(sides=sides)
    bpy.ops.object.mode_set(mode="OBJECT")


def triangulate(obj: Any) -> None:
    """Converte quads/n-gons em triângulos (fill_holes cria n-gons)."""
    import bpy

    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.quads_convert_to_tris(quad_method="BEAUTY", ngon_method="BEAUTY")
    bpy.ops.object.mode_set(mode="OBJECT")


def normals_consistent(obj: Any, inside: bool = False) -> None:
    """Recalcula normais consistentes (outward por defeito)."""
    import bpy

    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.normals_make_consistent(inside=inside)
    bpy.ops.object.mode_set(mode="OBJECT")


def dissolve_degenerate(obj: Any, threshold: float = 1e-6) -> None:
    """Dissolve faces/arestas degeneradas abaixo de ``threshold``."""
    import bpy

    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.dissolve_degenerate(threshold=threshold)
    bpy.ops.object.mode_set(mode="OBJECT")


def delete_loose(obj: Any) -> None:
    """Apaga vértices/arestas soltos (sem faces)."""
    import bpy

    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.delete_loose()
    bpy.ops.object.mode_set(mode="OBJECT")


def remove_loose_debris(obj: Any, *, face_ratio: float, min_faces: int) -> int:
    """Apaga ilhas minúsculas (floaters de MC/quantização) via union-find.

    Componentes com menos de ``max(min_faces, face_ratio * total_faces)``
    faces são removidos. A maior ilha nunca é apagada.

    Args:
        obj: bpy mesh object.
        face_ratio: Fração do total de faces abaixo da qual a ilha é debris
            (``<=0`` desativa).
        min_faces: Piso absoluto de faces para uma ilha sobreviver.

    Returns:
        Número de faces removidas.
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


def remove_long_edges(
    obj: Any, max_length: float, *, median_factor: float = 8.0, max_removal_ratio: float = 0.5
) -> int:
    """Remove faces com aresta outlier (triângulos degenerados de MC).

    Limiar efectivo = ``max(max_length, median_factor * mediana)`` para não
    destruir meshes grossas legítimas. Aborta se a selecção apagaria mais de
    ``max_removal_ratio`` das faces.

    Returns:
        Número de faces removidas.
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
    threshold = max(float(max_length), median_factor * median)

    doomed = [f for f in bm.faces if any(e.calc_length() > threshold for e in f.edges)]
    if not doomed or len(doomed) > max_removal_ratio * len(bm.faces):
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


def close_holes_and_repair(
    obj: Any,
    *,
    fill_sides: int = 30,
    degenerate_threshold: float = 1e-6,
    weld_threshold: float = 1e-6,
) -> None:
    """Sequência pós-remesh (estilo Text3D): loose → degenerate → fill → weld."""
    delete_loose(obj)
    dissolve_degenerate(obj, threshold=degenerate_threshold)
    fill_holes(obj, sides=fill_sides)
    remove_doubles(obj, threshold=weld_threshold)


# ---------------------------------------------------------------------------
# Conversões e saneamento
# ---------------------------------------------------------------------------


def mesh_to_trimesh(obj: Any) -> Any:
    """Converte bpy mesh object em ``trimesh.Trimesh`` (exige triângulos)."""
    import trimesh

    me = obj.data
    verts = np.array([tuple(v.co) for v in me.vertices], dtype=np.float64)
    faces = np.array([tuple(p.vertices) for p in me.polygons], dtype=np.int64)
    if verts.size == 0 or faces.size == 0:
        return trimesh.Trimesh(vertices=np.zeros((0, 3)), faces=np.zeros((0, 3), dtype=np.int64), process=False)
    return trimesh.Trimesh(vertices=verts, faces=faces, process=False)


def drop_nonfinite_faces(verts: np.ndarray, faces: np.ndarray) -> tuple[np.ndarray, np.ndarray, int]:
    """Remove faces que tocam vértices NaN/Inf.

    Verts não-finitos passam invisíveis pelas reparações (comparações com NaN
    dão ``False``) e o exporter glTF converte-os em (0,0,0) — leque de faces
    gigantes na origem.

    Returns:
        ``(verts_compactados, faces_remapeadas, n_faces_removidas)``.
    """
    finite = np.isfinite(verts).all(axis=1)
    if finite.all():
        return verts, faces, 0
    keep = finite[faces].all(axis=1)
    n_dropped = int((~keep).sum())
    # Compactar: vértices não-finitos saem do buffer (senão sobrevivem órfãos
    # no export) e as faces são remapeadas.
    remap = -np.ones(len(verts), dtype=np.int64)
    remap[finite] = np.arange(int(finite.sum()), dtype=np.int64)
    return verts[finite], remap[faces[keep]], n_dropped


# ---------------------------------------------------------------------------
# Boundary loops / cap seletivo
# ---------------------------------------------------------------------------


def boundary_loops(bm: Any) -> list[list[Any]]:
    """Agrupa arestas de fronteira (1 face ligada) em loops."""
    boundary = [e for e in bm.edges if len(e.link_faces) == 1]
    unvisited = set(boundary)
    loops: list[list[Any]] = []
    while unvisited:
        start = unvisited.pop()
        loop = [start]
        v = start.verts[1]
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


def loop_is_cappable(loop: list[Any], *, max_edges: int, planar_tol: float) -> bool:
    """Loop pequeno e ~planar → seguro tapar. Cortes gigantes/serpenteantes → não."""
    if len(loop) < 3 or len(loop) > max_edges:
        return False
    pts = np.array([v.co[:] for e in loop for v in e.verts], dtype=np.float64)
    center = pts.mean(axis=0)
    sv = np.linalg.svd(pts - center, compute_uv=False)
    diameter = float(np.linalg.norm(pts.max(axis=0) - pts.min(axis=0)))
    if diameter <= 0:
        return False
    thickness = float(sv[-1]) / float(np.sqrt(max(len(pts), 1)))
    return bool(thickness < planar_tol * diameter)


def cap_boundary_holes(mesh: Any, *, max_loop_edges: int = 400, planar_tol: float = 0.10) -> Any:
    """Fecha buracos de fronteira de uma parte extraída — só loops seguros.

    Tapar TODOS os loops (``fill_holes(sides=0)``) cria membranas gigantes
    sobre cortes não-planares (fachada inteira tapada por um "sólido"). Aqui
    só se tapam loops com ≤ ``max_loop_edges`` arestas e aproximadamente
    planares (desvio RMS < ``planar_tol`` x diâmetro).

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
    obj = create_mesh_from_arrays(verts, faces, name="gamedev_cap")
    try:
        remove_doubles(obj, threshold=1e-6)

        bpy.context.view_layer.objects.active = obj
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bm.edges.ensure_lookup_table()
        capped = 0
        for loop in boundary_loops(bm):
            if not loop_is_cappable(loop, max_edges=max_loop_edges, planar_tol=planar_tol):
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
            normals_consistent(obj)
            log.debug("cap_boundary_holes: %d loops fechados", capped)
        out = mesh_to_trimesh(obj)
        return out if len(out.faces) > 0 else mesh
    except Exception as exc:
        log.warning("cap_boundary_holes bpy falhou (%s); a devolver mesh original", exc)
        return mesh
    finally:
        clear_scene()


# ---------------------------------------------------------------------------
# Pipeline pós-decode (marching cubes)
# ---------------------------------------------------------------------------


def fix_mesh(
    mesh: Any,
    *,
    max_edge_length: float = 8.0 / 512.0,
    fill_holes_sides: int = 12,
    debris_face_ratio: float = 0.1,
    debris_min_faces: int = 8,
) -> Any:
    """Repara mesh pós-decode (marching cubes / DMC) com bpy.

    Ordem: NaN guard → weld exacto → weld relativo → long edges → debris →
    normais → fill holes pequenos → triangulate.

    Args:
        mesh: Objecto com ``.vertices`` e ``.faces`` (ex. ``trimesh.Trimesh``).
        max_edge_length: Limiar mínimo para remover faces de arestas longas.
        fill_holes_sides: Máx. lados para ``fill_holes`` (``0`` desativa).
        debris_face_ratio: Fração de faces para ilha contar como debris.
        debris_min_faces: Piso absoluto de faces por ilha.

    Returns:
        ``trimesh.Trimesh`` reparado (ou o input se vazio / falha não-crítica).
    """
    verts = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    if verts.size == 0 or faces.size == 0:
        return mesh

    verts, faces, n_bad = drop_nonfinite_faces(verts, faces)
    if n_bad:
        log.warning("fix_mesh: %d faces com vértices não-finitos removidas", n_bad)
        if faces.size == 0:
            return mesh

    clear_scene()
    obj = create_mesh_from_arrays(verts, faces, name="gamedev_fix")

    try:
        remove_doubles(obj, threshold=1e-5)
        bbox = obj.dimensions
        diag = float(np.linalg.norm([bbox.x, bbox.y, bbox.z])) or 1.0
        remove_doubles(obj, threshold=max(1e-4, 0.0001 * diag))
        n_long = remove_long_edges(obj, max_length=max_edge_length)
        n_debris = remove_loose_debris(obj, face_ratio=debris_face_ratio, min_faces=debris_min_faces)
        try:
            normals_consistent(obj)
        except Exception as exc:
            log.warning("normals_make_consistent falhou: %s", exc)
        if fill_holes_sides > 0:
            try:
                fill_holes(obj, sides=fill_holes_sides)
                triangulate(obj)  # fill_holes cria n-gons; trimesh exige triângulos
            except Exception as exc:
                log.warning("fill_holes falhou: %s", exc)
        if n_long or n_debris:
            log.debug("fix_mesh bpy: long_edges=%d debris_faces=%d", n_long, n_debris)
        return mesh_to_trimesh(obj)
    except Exception as exc:
        log.warning("fix_mesh bpy falhou (%s); a devolver mesh original", exc)
        return mesh
    finally:
        clear_scene()
