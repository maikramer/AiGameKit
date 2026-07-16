"""Reparação de mesh unificada via bpy — sem PyMeshLab, core sem trimesh.

Unifica os helpers que viviam duplicados em Text3D (``mesh_lod`` /
``mesh_remesh_textured``), Part3D (``utils/mesh_bpy``) e Paint3D
(``mesh_io``), na variante mais avançada de cada um, operando **in-place em
bpy objects** — UVs, materiais, vertex groups (skin weights) e shape keys são
preservados pelas operações bmesh/EDIT.

API principal (bpy-only):

* ``repair_mesh_object_with_profile`` — perfis ``topology_clean`` /
  ``pre_decimate_uv`` / ``post_decimate`` / ``part_decode`` / ``post_voxel``.
* ``repair_mesh_object`` — sequência completa in-place num mesh object;
  ``watertight=True`` chama :func:`make_watertight` no fim.
* ``repair_glb`` — file-level: importa GLB (texturas, armature, animações,
  morphs), repara cada mesh e re-exporta; com ``watertight=True`` exporta
  sem custom normals/tangents para o fecho não reabrir no round-trip glTF
  (o exporter parte vértices em arestas duras e cria falsas fronteiras).
* ``sanitize_nonfinite`` — remove vértices NaN/Inf (passam invisíveis nas
  comparações e viram (0,0,0) no export glTF — leque de faces na origem).
* ``cap_boundary_loops`` / ``make_watertight`` — fecho seletivo / total de
  boundary loops.
* ``reweld_coincident`` — colapsa vértices coincidentes (útil pós-import
  glTF com normal-split).
* ``dynamic_weld_distance`` — limiar de weld por densidade de vértices.

Fronteira de compatibilidade (Part3D/X-Part usa trimesh no decode):

* ``fix_mesh`` / ``cap_boundary_holes`` — contrato mesh-like → trimesh;
  ``fix_mesh(..., watertight=True)`` fecha o volume pós-MC.

Todas as funções importam ``bpy``/``bmesh``/``trimesh`` lazily — o módulo
pode ser importado em ambientes sem Blender.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any, Literal

import numpy as np

from gamedev_shared.bpy_mesh import clear_scene, create_mesh_from_arrays, save_glb

log = logging.getLogger(__name__)

WeldMode = Literal["bbox", "vert_density", "fixed", "none"]
RepairProfileName = Literal["topology_clean", "pre_decimate_uv", "part_decode", "post_voxel"]


# ---------------------------------------------------------------------------
# Primitivas sobre bpy objects (in-place; preservam UV/materiais/weights)
# ---------------------------------------------------------------------------


def sanitize_nonfinite(obj: Any) -> int:
    """Remove vértices NaN/Inf (e faces incidentes) de um bpy mesh object.

    Verts não-finitos passam invisíveis pelas reparações (comparações com NaN
    dão ``False``) e o exporter glTF converte-os em (0,0,0) — o clássico
    leque de faces gigantes na origem.

    Returns:
        Número de vértices removidos.
    """
    import bmesh

    me = obj.data
    bm = bmesh.new()
    bm.from_mesh(me)
    bad = [v for v in bm.verts if not (math.isfinite(v.co.x) and math.isfinite(v.co.y) and math.isfinite(v.co.z))]
    n = len(bad)
    if n:
        bmesh.ops.delete(bm, geom=bad, context="VERTS")
        bm.to_mesh(me)
        me.update()
    bm.free()
    return n


def remove_doubles(obj: Any, threshold: float) -> int:
    """Funde vértices duplicados dentro de ``threshold`` (bmesh, sem EDIT mode).

    UVs são por loop/corner — costuras UV sobrevivem ao weld.

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
    """Remove faces com aresta outlier (triângulos degenerados de MC / leques).

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


def remove_sliver_faces(
    obj: Any,
    *,
    max_aspect: float = 80.0,
    max_removal_ratio: float = 0.25,
) -> int:
    """Remove triângulos-agulha (aspecto = longest_edge² / area).

    Tipico em sparks/fuses/folhas finas após decimação agressiva: faces com
    área ~0 e arestas longas viram "estrelas" visuais. Aborta se apagaria
    mais de ``max_removal_ratio`` das faces (malha já quase só slivers).

    Returns:
        Número de faces removidas.
    """
    import bmesh

    me = obj.data
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.faces.ensure_lookup_table()

    if not bm.faces:
        bm.free()
        return 0

    doomed: list[Any] = []
    for f in bm.faces:
        area = float(f.calc_area())
        if area < 1e-18:
            doomed.append(f)
            continue
        longest = max((e.calc_length() for e in f.edges), default=0.0)
        if longest * longest / area > max_aspect:
            doomed.append(f)

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
# Boundary loops / cap seletivo (bpy in-place)
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


def _loop_plane_stats(
    loop: list[Any], world_matrix: np.ndarray | None = None
) -> tuple[np.ndarray, np.ndarray, float, float] | None:
    """Estatísticas do plano de um loop: (centroid, normal, thickness, diameter).

    ``world_matrix`` (4x4) transforma para espaço mundo — necessário quando a
    orientação vive na matrix do objeto (imports glTF), senão "baixo" local
    não é baixo real.
    """
    pts = np.array([v.co[:] for e in loop for v in e.verts], dtype=np.float64)
    if world_matrix is not None and not np.allclose(world_matrix, np.eye(4)):
        pts = (np.c_[pts, np.ones(len(pts))] @ world_matrix.T)[:, :3]
    center = pts.mean(axis=0)
    diameter = float(np.linalg.norm(pts.max(axis=0) - pts.min(axis=0)))
    if diameter <= 0:
        return None
    _u, sv, vt = np.linalg.svd(pts - center)
    thickness = float(sv[-1]) / float(np.sqrt(max(len(pts), 1)))
    normal = vt[-1]
    return center, normal, thickness, diameter


def loop_is_cappable(loop: list[Any], *, max_edges: int, planar_tol: float) -> bool:
    """Loop pequeno e ~planar → seguro tapar. Cortes gigantes/serpenteantes → não."""
    if len(loop) < 3 or len(loop) > max_edges:
        return False
    stats = _loop_plane_stats(loop)
    if stats is None:
        return False
    _center, _normal, thickness, diameter = stats
    return bool(thickness < planar_tol * diameter)


def _loop_is_base(
    loop: list[Any],
    bbox_min: np.ndarray,
    bbox_max: np.ndarray,
    *,
    planar_tol: float,
    normal_tol: float,
    band: float,
    world_matrix: np.ndarray | None = None,
) -> bool:
    """Loop de base: ~planar, normal alinhada a um eixo, na banda inferior da bbox.

    Avaliado em espaço mundo (``world_matrix``) — imports glTF guardam a
    orientação na matrix do objeto e "baixo" local pode ser o topo. Bases
    abertas (Hunyuan corta o fundo dos edifícios) podem ter milhares de
    arestas — sem limite de tamanho aqui; nunca dispara no topo (taças/canecas).
    """
    if len(loop) < 3:
        return False
    stats = _loop_plane_stats(loop, world_matrix)
    if stats is None:
        return False
    center, normal, thickness, diameter = stats
    if thickness >= 2.0 * planar_tol * diameter:
        return False
    axis = int(np.argmax(np.abs(normal)))
    if abs(float(normal[axis])) < normal_tol:
        return False
    extent = float(bbox_max[axis] - bbox_min[axis]) or 1.0
    return bool(float(center[axis]) - float(bbox_min[axis]) < band * extent)


def cap_boundary_loops(
    obj: Any,
    *,
    max_loop_edges: int = 400,
    planar_tol: float = 0.10,
    cap_base: bool = False,
    base_normal_tol: float = 0.8,
    base_band: float = 0.10,
) -> int:
    """Fecha boundary loops seguros de um bpy mesh object (in-place).

    Tapar TODOS os loops (``fill_holes(sides=0)``) cria membranas gigantes
    sobre cortes não-planares (fachada inteira tapada por um "sólido"). Aqui
    só se tapam loops com ≤ ``max_loop_edges`` arestas e aproximadamente
    planares (desvio RMS < ``planar_tol`` x diâmetro). Com ``cap_base=True``,
    loops planares horizontais na banda inferior da bbox (fundos abertos de
    edifícios) são tapados sem limite de arestas.

    Returns:
        Número de loops fechados.
    """
    import bmesh
    import bpy

    bpy.context.view_layer.objects.active = obj
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.edges.ensure_lookup_table()

    # Espaço mundo: imports glTF guardam a orientação na matrix do objeto —
    # sem ela, a banda "inferior" da bbox pode calhar no topo real.
    mw = np.array(obj.matrix_world, dtype=np.float64)
    world_matrix = None if np.allclose(mw, np.eye(4)) else mw

    bbox_min = bbox_max = None
    if cap_base and bm.verts:
        coords = np.array([v.co[:] for v in bm.verts], dtype=np.float64)
        if world_matrix is not None:
            coords = (np.c_[coords, np.ones(len(coords))] @ world_matrix.T)[:, :3]
        bbox_min, bbox_max = coords.min(axis=0), coords.max(axis=0)

    capped = 0
    for loop in boundary_loops(bm):
        ok = loop_is_cappable(loop, max_edges=max_loop_edges, planar_tol=planar_tol)
        if not ok and cap_base and bbox_min is not None and bbox_max is not None:
            ok = _loop_is_base(
                loop,
                bbox_min,
                bbox_max,
                planar_tol=planar_tol,
                normal_tol=base_normal_tol,
                band=base_band,
                world_matrix=world_matrix,
            )
        if not ok:
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
        log.debug("cap_boundary_loops: %d loops fechados", capped)
    return capped


def count_boundary_edges(obj: Any) -> int:
    """Arestas de fronteira (1 face) — 0 em meshes fechadas."""
    import bmesh

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    n = sum(1 for e in bm.edges if len(e.link_faces) == 1)
    bm.free()
    return n


def _is_near_planar_sheet(obj: Any, *, flatness: float = 0.02) -> bool:
    """True se a bbox é essencialmente 2D (folha aberta — não dá para watertight)."""
    dims = obj.dimensions
    vals = sorted(float(x) for x in (dims.x, dims.y, dims.z))
    if vals[2] <= 1e-12:
        return True
    return (vals[0] / vals[2]) < flatness


def make_watertight(
    obj: Any,
    *,
    planar_tol: float = 0.10,
    max_loop_edges: int = 400,
    cap_base: bool = True,
    final_fill: bool = True,
) -> dict[str, int]:
    """Fecha uma mesh até watertight (só bpy/bmesh), do seguro para o bruto.

    Camadas: (1) caps planares seletivos + base aberta; (2) ``fill_holes``
    progressivo (12 → 64 lados) para rachas; (3) ``final_fill``: fill_holes
    sem limite nos loops restantes — garante fecho, ao custo de membranas em
    loops não-planares residuais (raros depois das camadas anteriores).

    Folhas quase-planares (bbox 2D) saltam o fecho agressivo — não têm volume
    para fechar e o pinch/erode pode apagar todas as faces.

    Returns:
        ``{"boundary_before", "loops_capped", "boundary_after"}``.
    """
    stats: dict[str, int] = {"boundary_before": count_boundary_edges(obj)}
    if stats["boundary_before"] == 0:
        stats["loops_capped"] = 0
        stats["boundary_after"] = 0
        return stats

    faces_before = len(obj.data.polygons)
    planar_sheet = _is_near_planar_sheet(obj)

    stats["loops_capped"] = cap_boundary_loops(
        obj, max_loop_edges=max_loop_edges, planar_tol=planar_tol, cap_base=cap_base
    )
    for sides in (12, 64):
        if count_boundary_edges(obj) == 0:
            break
        with_suppress_fill(obj, sides)

    if final_fill and not planar_sheet and count_boundary_edges(obj) > 0:
        with_suppress_fill(obj, 0)
    if final_fill and not planar_sheet and count_boundary_edges(obj) > 0:
        # fill_holes global falha em loops que tocam vértices non-manifold;
        # holes_fill do bmesh loop-a-loop é mais robusto como último recurso.
        stats["loops_forced"] = _cap_all_remaining_loops(obj)
    if final_fill and not planar_sheet:
        # Micro-rachas restantes são cadeias não-fechadas presas em junções
        # non-manifold (holes_fill exige loops fechados). Pinch dirigido:
        # weld localizado só nos verts de cada racha, depois re-fill.
        prev = count_boundary_edges(obj)
        for _ in range(3):
            if prev == 0:
                break
            stats["cracks_pinched"] = stats.get("cracks_pinched", 0) + _pinch_small_boundary_chains(obj)
            with_suppress_fill(obj, 0)
            _cap_all_remaining_loops(obj)
            cur = count_boundary_edges(obj)
            if cur >= prev:
                break
            prev = cur
        if count_boundary_edges(obj) > 0:
            # Bordos de abas de parede interna (MC) não são weldáveis nem
            # tapáveis: erodir as abas até à junção manifold e re-tapar.
            stats["flap_faces_eroded"] = _erode_boundary_flaps(obj)
            with_suppress_fill(obj, 0)
            _cap_all_remaining_loops(obj)
    elif planar_sheet:
        stats["planar_sheet_skip_aggressive"] = 1
        log.info("make_watertight: folha quase-planar — a saltar fill/pinch/erode agressivos")

    # Guarda: nunca exportar mesh vazia se ainda havia faces.
    if faces_before > 0 and len(obj.data.polygons) == 0:
        log.warning(
            "make_watertight: operações deixaram 0 faces (tinha %d) — mesh corrompida",
            faces_before,
        )

    # fill_holes cria n-gons; exportadores/consumidores esperam triângulos.
    try:
        if len(obj.data.polygons) > 0:
            triangulate(obj)
            normals_consistent(obj)
    except Exception as exc:
        log.warning("triangulate/normals pós-watertight falhou: %s", exc)
    stats["boundary_after"] = count_boundary_edges(obj)
    if stats["boundary_after"]:
        log.warning("make_watertight: %d arestas de fronteira restantes", stats["boundary_after"])
    return stats


def with_suppress_fill(obj: Any, sides: int) -> None:
    """``fill_holes`` tolerante a falhas (usado pelo :func:`make_watertight`)."""
    try:
        fill_holes(obj, sides=sides)
    except Exception as exc:
        log.warning("fill_holes(sides=%d) falhou: %s", sides, exc)


def _erode_boundary_flaps(obj: Any, *, max_iters: int = 6, max_faces_ratio: float = 0.01) -> int:
    """Erode faces penduradas em arestas de fronteira (abas internas do MC).

    Cada passada apaga faces com ≥1 aresta boundary; abas finas encolhem até
    à junção manifold, deixando loops fecháveis. Guard: nunca remove mais de
    ``max_faces_ratio`` do total de faces.

    Returns:
        Faces removidas.
    """
    import bmesh

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    total = len(bm.faces)
    budget = max(1, int(max_faces_ratio * total))
    removed = 0
    for _ in range(max_iters):
        bm.edges.ensure_lookup_table()
        doomed = list({f for e in bm.edges if len(e.link_faces) == 1 for f in e.link_faces})
        if not doomed or removed + len(doomed) > budget:
            break
        removed += len(doomed)
        bmesh.ops.delete(bm, geom=doomed, context="FACES")
        orphans = [v for v in bm.verts if not v.link_faces]
        if orphans:
            bmesh.ops.delete(bm, geom=orphans, context="VERTS")
    if removed:
        bm.to_mesh(obj.data)
        obj.data.update()
    bm.free()
    return removed


def _pinch_small_boundary_chains(obj: Any, *, max_edges: int = 64, dist_factor: float = 2.0) -> int:
    """Funde micro-rachas de fronteira via weld localizado (pinch).

    Cadeias boundary não-fechadas (presas em junções non-manifold) não são
    tapáveis por ``holes_fill``. Weld só entre os verts da racha, com
    distância proporcional à aresta média da própria racha — não toca no
    resto da mesh.

    Returns:
        Número de cadeias processadas.
    """
    import bmesh

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.edges.ensure_lookup_table()
    pinched = 0
    for chain in boundary_loops(bm):
        if not chain or len(chain) > max_edges:
            continue
        edges = [e for e in chain if e.is_valid]
        if not edges:
            continue
        verts = list({v for e in edges for v in e.verts if v.is_valid})
        if len(verts) < 2:
            continue
        mean_len = float(np.mean([e.calc_length() for e in edges])) or 0.0
        if mean_len <= 0:
            continue
        try:
            bmesh.ops.remove_doubles(bm, verts=verts, dist=dist_factor * mean_len)
            pinched += 1
        except Exception:
            continue
    if pinched:
        bm.to_mesh(obj.data)
        obj.data.update()
    bm.free()
    return pinched


def _cap_all_remaining_loops(obj: Any) -> int:
    """Fecha TODOS os boundary loops restantes via bmesh (último recurso)."""
    import bmesh

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.edges.ensure_lookup_table()
    capped = 0
    for loop in boundary_loops(bm):
        edges = [e for e in loop if e.is_valid]
        if len(edges) < 3:
            continue
        try:
            bmesh.ops.holes_fill(bm, edges=edges, sides=0)
            capped += 1
        except Exception:
            try:
                bmesh.ops.contextual_create(bm, geom=edges)
                capped += 1
            except Exception:
                continue
    bm.to_mesh(obj.data)
    obj.data.update()
    bm.free()
    return capped


# ---------------------------------------------------------------------------
# Perfis nomeados + weld adaptativo
# ---------------------------------------------------------------------------


def dynamic_weld_distance(vertex_count: int) -> float:
    """Distância de weld adaptativa pela densidade de vértices (topology-fix).

    Malhas densas (>150k) usam limiar menor para preservar detalhe; malhas
    leves (<50k) usam limiar maior para fechar rachaduras de marching cubes.
    """
    if vertex_count > 150_000:
        return 0.003
    if vertex_count > 100_000:
        return 0.005
    if vertex_count > 50_000:
        return 0.008
    return 0.01


@dataclass(frozen=True)
class RepairProfile:
    """Opções canónicas para :func:`repair_mesh_object` / perfis nomeados."""

    name: str = "custom"
    weld_threshold: float = 1e-5
    weld_mode: WeldMode = "bbox"
    weld_relative: float = 0.0001
    weld_fixed: float = 0.0005
    do_exact_weld: bool = True
    do_sanitize: bool = True
    do_dissolve_loose: bool = True
    degenerate_threshold: float = 1e-6
    do_long_edges: bool = True
    long_edge_length: float = 8.0 / 512.0
    long_edge_median_factor: float = 8.0
    sliver_max_aspect: float | None = None
    debris_face_ratio: float = 0.0005
    debris_min_faces: int = 64
    fill_holes_sides: int = 12
    triangulate_after_fill: bool = True
    cap_holes: bool = False
    cap_max_loop_edges: int = 400
    cap_planar_tol: float = 0.10
    watertight: bool = False
    watertight_planar_tol: float = 0.15
    watertight_max_loop_edges: int = 400
    watertight_cap_base: bool = True
    watertight_final_fill: bool = True
    recalc_normals: bool = True
    # Pré-passo: colapsar normal-splits do glTF (topology-fix em GLB).
    do_reweld_coincident: bool = False
    reweld_threshold: float = 1e-6
    # Atalho pós-voxel remesh (não usa a cadeia completa).
    use_close_holes_and_repair: bool = False
    close_holes_fill_sides: int = 30
    # Atalho pós-Decimate COLLAPSE (dissolve + weld exacto + slivers + tri).
    use_post_decimate_cleanup: bool = False
    post_decimate_weld: float = 1e-5
    post_decimate_sliver_aspect: float = 80.0


REPAIR_PROFILES: dict[str, RepairProfile] = {
    "pre_decimate_uv": RepairProfile(
        name="pre_decimate_uv",
        do_exact_weld=False,
        weld_mode="fixed",
        weld_fixed=0.0005,
        sliver_max_aspect=80.0,
        fill_holes_sides=12,  # micro-cracks MC; aberturas grandes (>12) ficam
        watertight=False,
        # Conservador: dissolve/loose já corre no pós-decimate.
        do_dissolve_loose=False,
    ),
    "post_decimate": RepairProfile(
        name="post_decimate",
        use_post_decimate_cleanup=True,
        post_decimate_weld=1e-5,
        post_decimate_sliver_aspect=80.0,
        recalc_normals=False,
    ),
    "part_decode": RepairProfile(
        name="part_decode",
        weld_mode="bbox",
        debris_face_ratio=0.1,
        debris_min_faces=8,
        sliver_max_aspect=None,
        # Pós-MC: fecho por defeito no fix_mesh via override; perfil base sem
        # watertight para quem quiser só limpeza (face-split / shells).
        watertight=False,
    ),
    "topology_clean": RepairProfile(
        name="topology_clean",
        weld_mode="vert_density",
        do_exact_weld=True,
        do_reweld_coincident=True,
        sliver_max_aspect=80.0,
        fill_holes_sides=64,
        watertight=True,
        watertight_planar_tol=0.15,
    ),
    "post_voxel": RepairProfile(
        name="post_voxel",
        use_close_holes_and_repair=True,
        close_holes_fill_sides=30,
        recalc_normals=True,
    ),
}


def get_repair_profile(name: str | RepairProfile) -> RepairProfile:
    """Resolve nome de perfil ou devolve a instância se já for ``RepairProfile``."""
    if isinstance(name, RepairProfile):
        return name
    key = str(name).strip().lower()
    if key not in REPAIR_PROFILES:
        known = ", ".join(sorted(REPAIR_PROFILES))
        raise ValueError(f"Perfil de reparo desconhecido: {name!r}. Conhecidos: {known}")
    return REPAIR_PROFILES[key]


def post_decimate_cleanup(
    obj: Any,
    *,
    weld_threshold: float = 1e-5,
    sliver_max_aspect: float = 80.0,
    degenerate_threshold: float = 1e-6,
) -> dict[str, int]:
    """Limpeza leve após Decimate COLLAPSE (preserva UVs).

    dissolve → weld exacto → slivers → triangulate. Sem watertight/debris
    (o Decimate já alterou a topologia; o objectivo é só remover agulhas).
    """
    stats: dict[str, int] = {}
    try:
        dissolve_degenerate(obj, threshold=degenerate_threshold)
    except Exception as exc:
        log.warning("dissolve_degenerate pós-decimate falhou: %s", exc)
    if weld_threshold > 0:
        stats["welded_exact"] = remove_doubles(obj, threshold=weld_threshold)
    if sliver_max_aspect > 0:
        try:
            stats["sliver_faces"] = remove_sliver_faces(obj, max_aspect=sliver_max_aspect)
        except Exception as exc:
            log.warning("remove_sliver_faces pós-decimate falhou: %s", exc)
            stats["sliver_faces"] = 0
    try:
        triangulate(obj)
    except Exception as exc:
        log.warning("triangulate pós-decimate falhou: %s", exc)
    return stats


def repair_mesh_object_with_profile(
    obj: Any,
    profile: str | RepairProfile,
    **overrides: Any,
) -> dict[str, int]:
    """Aplica um perfil nomeado (com overrides opcionais) a um bpy mesh object."""
    prof = get_repair_profile(profile)
    if overrides:
        valid = {f.name for f in fields(RepairProfile)} - {"name"}
        unknown = set(overrides) - valid
        if unknown:
            raise TypeError(f"Overrides inválidos para RepairProfile: {sorted(unknown)}")
        prof = replace(prof, **overrides)

    if prof.use_post_decimate_cleanup:
        stats = post_decimate_cleanup(
            obj,
            weld_threshold=prof.post_decimate_weld,
            sliver_max_aspect=prof.post_decimate_sliver_aspect,
            degenerate_threshold=prof.degenerate_threshold,
        )
        stats["profile_post_decimate"] = 1
        return stats

    if prof.use_close_holes_and_repair:
        stats = {"profile_post_voxel": 1}
        close_holes_and_repair(
            obj,
            fill_sides=prof.close_holes_fill_sides,
            degenerate_threshold=prof.degenerate_threshold,
            weld_threshold=1e-6,
        )
        if prof.recalc_normals:
            try:
                normals_consistent(obj)
                stats["normals"] = 1
            except Exception as exc:
                log.warning("normals_make_consistent falhou: %s", exc)
        return stats

    n = reweld_coincident(obj, threshold=prof.reweld_threshold) if prof.do_reweld_coincident else 0

    stats = repair_mesh_object(
        obj,
        weld_threshold=prof.weld_threshold,
        weld_mode=prof.weld_mode,
        weld_relative=prof.weld_relative,
        weld_fixed=prof.weld_fixed,
        do_exact_weld=prof.do_exact_weld,
        do_sanitize=prof.do_sanitize,
        do_dissolve_loose=prof.do_dissolve_loose,
        degenerate_threshold=prof.degenerate_threshold,
        do_long_edges=prof.do_long_edges,
        long_edge_length=prof.long_edge_length,
        long_edge_median_factor=prof.long_edge_median_factor,
        sliver_max_aspect=prof.sliver_max_aspect,
        debris_face_ratio=prof.debris_face_ratio,
        debris_min_faces=prof.debris_min_faces,
        fill_holes_sides=prof.fill_holes_sides,
        triangulate_after_fill=prof.triangulate_after_fill,
        cap_holes=prof.cap_holes,
        cap_max_loop_edges=prof.cap_max_loop_edges,
        cap_planar_tol=prof.cap_planar_tol,
        watertight=prof.watertight,
        watertight_planar_tol=prof.watertight_planar_tol,
        watertight_max_loop_edges=prof.watertight_max_loop_edges,
        watertight_cap_base=prof.watertight_cap_base,
        watertight_final_fill=prof.watertight_final_fill,
        recalc_normals=prof.recalc_normals,
    )
    if n:
        stats["rewelded_coincident"] = n
    return stats


def reweld_coincident(obj: Any, *, threshold: float = 1e-6) -> int:
    """Colapsa vértices coincidentes (ex. normal-split do glTF).

    O exporter glTF parte vértices em arestas com normais distintas; no
    reimport essas cópias aparecem como arestas de fronteira falsas até um
    weld. Usa limiar absoluto pequeno para não fundir geometria intencional.

    Returns:
        Número de vértices removidos pelo weld.
    """
    return remove_doubles(obj, threshold=threshold)


def repair_mesh_object(
    obj: Any,
    *,
    weld_threshold: float = 1e-5,
    weld_mode: WeldMode = "bbox",
    weld_relative: float = 0.0001,
    weld_fixed: float = 0.0005,
    do_exact_weld: bool = True,
    do_sanitize: bool = True,
    do_dissolve_loose: bool = True,
    degenerate_threshold: float = 1e-6,
    do_long_edges: bool = True,
    long_edge_length: float = 8.0 / 512.0,
    long_edge_median_factor: float = 8.0,
    sliver_max_aspect: float | None = None,
    debris_face_ratio: float = 0.0005,
    debris_min_faces: int = 64,
    fill_holes_sides: int = 12,
    triangulate_after_fill: bool = True,
    cap_holes: bool = False,
    cap_max_loop_edges: int = 400,
    cap_planar_tol: float = 0.10,
    watertight: bool = False,
    watertight_planar_tol: float = 0.15,
    watertight_max_loop_edges: int = 400,
    watertight_cap_base: bool = True,
    watertight_final_fill: bool = True,
    recalc_normals: bool = True,
) -> dict[str, int]:
    """Sequência completa de reparação in-place num bpy mesh object.

    Preserva UVs, materiais, vertex groups (skin weights) e não toca em
    armatures/animações. Objetos com shape keys só recebem passos não
    destrutivos (welds/deletes corromperiam os morph targets).

    Ordem: NaN guard → weld exacto → weld secundário (bbox / densidade /
    fixed) → degenerate → loose → long edges → slivers → debris → fill holes
    → cap loops (opt) → ``make_watertight`` (opt) → normais.

    Preferir :func:`repair_mesh_object_with_profile` com perfis
    ``topology_clean`` / ``pre_decimate_uv`` / ``part_decode``.

    Returns:
        Estatísticas por passo (contagens de removidos/tapados). Inclui
        ``boundary_before`` / ``boundary_after`` quando ``watertight=True``.
    """
    stats: dict[str, int] = {}

    has_shape_keys = bool(getattr(obj.data, "shape_keys", None))
    if has_shape_keys:
        log.warning("repair_mesh_object: '%s' tem shape keys — só passos não destrutivos", obj.name)
        if recalc_normals:
            try:
                normals_consistent(obj)
                stats["normals"] = 1
            except Exception as exc:
                log.warning("normals_make_consistent falhou: %s", exc)
        return stats

    if do_sanitize:
        stats["nonfinite_verts"] = sanitize_nonfinite(obj)
    if do_exact_weld and weld_threshold > 0:
        stats["welded_exact"] = remove_doubles(obj, threshold=weld_threshold)

    mode = (weld_mode or "bbox").strip().lower()
    if mode == "bbox" and weld_relative > 0:
        bbox = obj.dimensions
        diag = float(np.linalg.norm([bbox.x, bbox.y, bbox.z])) or 1.0
        stats["welded_relative"] = remove_doubles(obj, threshold=max(1e-4, weld_relative * diag))
    elif mode == "vert_density":
        dist = dynamic_weld_distance(len(obj.data.vertices))
        stats["welded_relative"] = remove_doubles(obj, threshold=dist)
        stats["weld_distance"] = int(dist * 1_000_000)  # µm-ish for debug logs
    elif mode == "fixed" and weld_fixed > 0:
        stats["welded_relative"] = remove_doubles(obj, threshold=weld_fixed)

    if do_dissolve_loose:
        try:
            dissolve_degenerate(obj, threshold=degenerate_threshold)
            delete_loose(obj)
        except Exception as exc:
            log.warning("dissolve/loose falhou: %s", exc)
    if do_long_edges:
        stats["long_edge_faces"] = remove_long_edges(
            obj, max_length=long_edge_length, median_factor=long_edge_median_factor
        )
    if sliver_max_aspect is not None and sliver_max_aspect > 0:
        try:
            stats["sliver_faces"] = remove_sliver_faces(obj, max_aspect=sliver_max_aspect)
        except Exception as exc:
            log.warning("remove_sliver_faces falhou: %s", exc)
            stats["sliver_faces"] = 0
    stats["debris_faces"] = remove_loose_debris(obj, face_ratio=debris_face_ratio, min_faces=debris_min_faces)
    if fill_holes_sides > 0:
        try:
            fill_holes(obj, sides=fill_holes_sides)
            if triangulate_after_fill:
                triangulate(obj)  # fill_holes cria n-gons
            stats["holes_filled"] = 1
        except Exception as exc:
            log.warning("fill_holes falhou: %s", exc)
    if cap_holes:
        stats["loops_capped"] = cap_boundary_loops(obj, max_loop_edges=cap_max_loop_edges, planar_tol=cap_planar_tol)
    if watertight:
        try:
            wt = make_watertight(
                obj,
                planar_tol=watertight_planar_tol,
                max_loop_edges=watertight_max_loop_edges,
                cap_base=watertight_cap_base,
                final_fill=watertight_final_fill,
            )
            stats["boundary_before"] = int(wt.get("boundary_before", 0))
            stats["boundary_after"] = int(wt.get("boundary_after", 0))
            stats["loops_capped"] = int(stats.get("loops_capped", 0)) + int(wt.get("loops_capped", 0))
            if wt.get("loops_forced"):
                stats["loops_forced"] = int(wt["loops_forced"])
            if wt.get("cracks_pinched"):
                stats["cracks_pinched"] = int(wt["cracks_pinched"])
            if wt.get("flap_faces_eroded"):
                stats["flap_faces_eroded"] = int(wt["flap_faces_eroded"])
        except Exception as exc:
            log.warning("make_watertight falhou: %s", exc)
            stats["boundary_after"] = count_boundary_edges(obj)
    if recalc_normals:
        try:
            normals_consistent(obj)
        except Exception as exc:
            log.warning("normals_make_consistent falhou: %s", exc)
    return stats


def repair_glb(
    input_path: str | Path,
    output_path: str | Path,
    *,
    meshopt: bool = False,
    image_format: str = "AUTO",
    watertight: bool = False,
    seal_export: bool | None = None,
    **repair_opts: Any,
) -> dict[str, dict[str, int]]:
    """Repara um GLB completo preservando texturas, rigging e animações.

    Importa via bpy (armatures, skin weights, animações e morphs entram na
    cena), repara cada mesh object com :func:`repair_mesh_object` e
    re-exporta a cena inteira. ``meshopt=True`` ativa a compressão
    EXT_meshopt_compression no exporter (quantização de buffers); KTX2/UASTC
    de texturas continua via ``text3d bake-master`` / ``@gltf-transform/cli``.

    Args:
        input_path: GLB/GLTF de entrada.
        output_path: GLB de saída.
        meshopt: Ativar compressão meshopt no export (requer bpy 5.2+).
        image_format: ``AUTO`` preserva o formato original das texturas;
            ``JPEG``/``PNG``/``WEBP`` forçam re-encode.
        watertight: Fecha volumes via :func:`make_watertight` em cada mesh.
        seal_export: Se True, exporta sem normals/tangents para o fecho
            sobreviver ao round-trip glTF. Default: igual a ``watertight``.
        **repair_opts: Encaminhados para :func:`repair_mesh_object`.

    Returns:
        Estatísticas por mesh object (nome → contagens por passo).
    """
    import bpy

    if seal_export is None:
        seal_export = bool(watertight)
    repair_opts = dict(repair_opts)
    repair_opts["watertight"] = bool(watertight)

    # bone_heuristic BLENDER (default) cria meshes "Icosphere" como display
    # shapes dos bones — seriam re-exportados como geometria real. TEMPERANCE
    # é a heurística de round-trip (sem custom shapes; skinning idêntico).
    clear_scene()
    bpy.ops.import_scene.gltf(filepath=str(Path(input_path).expanduser().resolve()), bone_heuristic="TEMPERANCE")
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    all_stats: dict[str, dict[str, int]] = {}
    for obj in meshes:
        # Pós-import: colapsar normal-splits do GLB anterior antes do fecho.
        if watertight:
            reweld_coincident(obj)
        all_stats[str(obj.name)] = repair_mesh_object(obj, **repair_opts)
    export_kw: dict[str, Any] = {
        "meshopt": meshopt,
        "export_image_format": image_format,
    }
    if seal_export:
        # Sem custom normals/tangents o exporter não parte vértices em arestas
        # "duras" — caso contrário o fecho watertight reabre no reimport.
        export_kw["export_normals"] = False
        export_kw["export_tangents"] = False
    # Exportar a cena inteira (None) — inclui armatures, animações e morphs.
    save_glb(None, output_path, **export_kw)
    log.info(
        "repair_glb: %d meshes reparados → %s (watertight=%s seal_export=%s)",
        len(meshes),
        output_path,
        watertight,
        seal_export,
    )
    return all_stats


# ---------------------------------------------------------------------------
# Saneamento em arrays (numpy puro)
# ---------------------------------------------------------------------------


def drop_nonfinite_faces(verts: np.ndarray, faces: np.ndarray) -> tuple[np.ndarray, np.ndarray, int]:
    """Remove faces que tocam vértices NaN/Inf e compacta o vertex buffer.

    Returns:
        ``(verts_compactados, faces_remapeadas, n_faces_removidas)``.
    """
    finite = np.isfinite(verts).all(axis=1)
    if finite.all():
        return verts, faces, 0
    keep = finite[faces].all(axis=1)
    n_dropped = int((~keep).sum())
    remap = -np.ones(len(verts), dtype=np.int64)
    remap[finite] = np.arange(int(finite.sum()), dtype=np.int64)
    return verts[finite], remap[faces[keep]], n_dropped


# ---------------------------------------------------------------------------
# Fronteira de compatibilidade trimesh (Part3D/X-Part decode)
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


def fix_mesh(
    mesh: Any,
    *,
    max_edge_length: float = 8.0 / 512.0,
    fill_holes_sides: int = 12,
    debris_face_ratio: float = 0.1,
    debris_min_faces: int = 8,
    watertight: bool = True,
) -> Any:
    """Repara mesh pós-decode (marching cubes / DMC) — contrato mesh-like → trimesh.

    Fronteira de compat para pipelines trimesh (Part3D/X-Part). Internamente
    delega no perfil ``part_decode`` via :func:`repair_mesh_object_with_profile`.
    Com ``watertight=True`` (defeito) fecha o volume via :func:`make_watertight`.

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
        stats = repair_mesh_object_with_profile(
            obj,
            "part_decode",
            long_edge_length=max_edge_length,
            debris_face_ratio=debris_face_ratio,
            debris_min_faces=debris_min_faces,
            fill_holes_sides=fill_holes_sides,
            watertight=watertight,
        )
        if stats.get("long_edge_faces") or stats.get("debris_faces") or stats.get("boundary_before"):
            log.debug(
                "fix_mesh bpy: long_edges=%d debris_faces=%d boundary %s→%s",
                stats.get("long_edge_faces", 0),
                stats.get("debris_faces", 0),
                stats.get("boundary_before", "?"),
                stats.get("boundary_after", "?"),
            )
        out = mesh_to_trimesh(obj)
        return out if len(out.faces) > 0 else mesh
    except Exception as exc:
        log.warning("fix_mesh bpy falhou (%s); a devolver mesh original", exc)
        return mesh
    finally:
        clear_scene()


def cap_boundary_holes(mesh: Any, *, max_loop_edges: int = 400, planar_tol: float = 0.10) -> Any:
    """Fecha buracos de fronteira seguros — contrato mesh-like → trimesh.

    Fronteira de compat (face-split do Part3D). Ver :func:`cap_boundary_loops`.

    Returns:
        ``trimesh.Trimesh`` com os loops seguros fechados (input se falha).
    """
    verts = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    if verts.size == 0 or faces.size == 0:
        return mesh

    clear_scene()
    obj = create_mesh_from_arrays(verts, faces, name="gamedev_cap")
    try:
        remove_doubles(obj, threshold=1e-6)
        cap_boundary_loops(obj, max_loop_edges=max_loop_edges, planar_tol=planar_tol)
        out = mesh_to_trimesh(obj)
        return out if len(out.faces) > 0 else mesh
    except Exception as exc:
        log.warning("cap_boundary_holes bpy falhou (%s); a devolver mesh original", exc)
        return mesh
    finally:
        clear_scene()
