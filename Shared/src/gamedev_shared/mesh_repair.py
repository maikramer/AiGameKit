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
* ``force_close_base`` — grelha planar flush na soleira se oco (shell sem chão)
  (``recess_ratio`` alto); skip torres/props com base já ok.
* ``clamp_base_flare`` — puxa “pés de elefante” MC (base mais larga que o
  corpo) para o raio de referência do meio do mesh.
* ``taubin_smooth`` — suavização volume-preserving (reduz argila MC sem
  derreter finos como um remesh).
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


def _fibonacci_sphere_dirs(n: int) -> np.ndarray:
    """Direções unitárias uniformes na esfera (Fibonacci)."""
    n = max(1, int(n))
    i = np.arange(n, dtype=np.float64)
    phi = np.pi * (3.0 - np.sqrt(5.0))
    y = 1.0 - (2.0 * i + 1.0) / n
    r = np.sqrt(np.maximum(0.0, 1.0 - y * y))
    theta = phi * i
    return np.stack([np.cos(theta) * r, y, np.sin(theta) * r], axis=1)


def remove_faces_invisible_from_exterior(
    obj: Any,
    *,
    n_dirs: int = 128,
    samples_per_dir: int = 48,
    max_removal_ratio: float = 0.75,
    seed: int = 0,
) -> int:
    """Apaga faces nunca atingidas por raios desde fora do AABB.

    Ideal para shells ocas (capela, torres): interiores / cascas internas não
    recebem hits desde a esfera exterior. Conservador em props sólidos —
    faces em cavidades profundas ainda são vistas de ângulos oblíquos.

    Args:
        obj: bpy mesh object.
        n_dirs: Câmaras na esfera de Fibonacci.
        samples_per_dir: Raios por câmara (alvos jitter no AABB).
        max_removal_ratio: Abort se a selecção ultrapassar esta fracção.
        seed: RNG para jitter dos alvos.

    Returns:
        Faces removidas (0 se abort/noop).
    """
    import bmesh
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree

    me = obj.data
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.faces.ensure_lookup_table()
    if not bm.faces:
        bm.free()
        return 0

    coords = np.array([v.co[:] for v in bm.verts], dtype=np.float64)
    center = coords.mean(axis=0)
    mins = coords.min(axis=0)
    maxs = coords.max(axis=0)
    extent = float(np.linalg.norm(maxs - mins)) or 1.0
    radius = extent * 0.65

    bvh = BVHTree.FromBMesh(bm, epsilon=max(1e-7, 1e-6 * extent))
    visible: set[int] = set()
    rng = np.random.default_rng(int(seed))
    dirs = _fibonacci_sphere_dirs(n_dirs)
    for d in dirs:
        cam = center + d * radius
        cam_v = Vector(cam.tolist())
        targets = rng.uniform(mins, maxs, size=(int(samples_per_dir), 3))
        for target in targets:
            direction = target - cam
            dist = float(np.linalg.norm(direction))
            if dist < 1e-9:
                continue
            direction /= dist
            hit = bvh.ray_cast(cam_v, Vector(direction.tolist()), dist)
            if hit[0] is None or hit[2] is None:
                continue
            visible.add(int(hit[2]))

    if not visible:
        bm.free()
        return 0

    doomed = [f for f in bm.faces if f.index not in visible]
    if not doomed:
        bm.free()
        return 0

    total = len(bm.faces)
    max_remove = max(1, int(float(max_removal_ratio) * total))
    if len(doomed) > max_remove:
        log.warning(
            "remove_faces_invisible_from_exterior: abort %d/%d (max_ratio=%.2f)",
            len(doomed),
            total,
            max_removal_ratio,
        )
        bm.free()
        return 0

    removed = len(doomed)
    bmesh.ops.delete(bm, geom=doomed, context="FACES")
    orphans = [v for v in bm.verts if not v.link_faces]
    if orphans:
        bmesh.ops.delete(bm, geom=orphans, context="VERTS")
    bm.to_mesh(me)
    me.update()
    bm.free()
    if removed:
        log.info(
            "remove_faces_invisible_from_exterior: %d faces (kept %d/%d)",
            removed,
            total - removed,
            total,
        )
    return removed


def remove_internal_shell_faces(
    obj: Any,
    *,
    wall_gap_ratio: float = 0.12,
    max_removal_ratio: float = 0.55,
    opposite_dot: float = -0.2,
    # Compat / deprecated.
    room_gap_ratio: float | None = None,
    gap_ratio: float | None = None,
) -> int:
    """Apaga faces do sanduíche fino de paredes duplas Hunyuan / MC.

    Só remove faces cujo ``+N`` (e/ou ``-N``) bate noutra face **dentro de
    ``wall_gap``** com normal aproximadamente oposta — o vão fino entre as
    duas paredes. Não usa hit à escala do oco/edifício: isso apagava props
    interiores (sino na torre, mobiliário) e interiores legítimos.

    * Fachada exterior: ``+N`` escapa → sobrevive.
    * Face a olhar para o vão fino: ``+N`` curto + normal oposta → remove.
    * Face a olhar para o oco grande: ``+N`` longo → sobrevive.

    Guard: se a selecção ultrapassar ``max_removal_ratio``, aborta (0 removidos).

    Args:
        obj: bpy mesh object.
        wall_gap_ratio: Fração da diagonal AABB = espessura máxima do sanduíche.
        max_removal_ratio: Fração máxima de faces removíveis.
        opposite_dot: Limiar ``|n·hit_n|`` — aceita anti/paralelo (≥ ``|opp|``).
        room_gap_ratio: Deprecated — ignorado (causava false positives).
        gap_ratio: Deprecated — se dado, usa-se como ``wall_gap_ratio``.

    Returns:
        Número de faces removidas (0 se abort/noop).
    """
    del room_gap_ratio  # deprecated no-op
    if gap_ratio is not None:
        wall_gap_ratio = float(gap_ratio)

    import bmesh
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree

    me = obj.data
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.faces.ensure_lookup_table()
    bm.normal_update()

    if not bm.faces:
        bm.free()
        return 0

    coords = np.array([v.co[:] for v in bm.verts], dtype=np.float64)
    diag = float(np.linalg.norm(coords.max(axis=0) - coords.min(axis=0))) or 1.0
    wall_gap = max(float(wall_gap_ratio) * diag, 1e-5)
    eps = max(1e-6, 1e-5 * diag)
    opp = float(opposite_dot)

    bvh = BVHTree.FromBMesh(bm, epsilon=eps * 0.1)
    doomed: list[Any] = []

    def _thin_twin(origin: Any, direction: Any, face_n: Any) -> bool:
        hit = bvh.ray_cast(origin, direction, wall_gap)
        if hit[0] is None or hit[1] is None:
            return False
        hn = Vector(hit[1])
        if hn.length_squared < 1e-12:
            return False
        hn.normalize()
        d = float(face_n.dot(hn))
        # Antiparalelo (olham uma para a outra) OU paralelo (bate nas costas
        # da gémea — shells nesteds com normals ambas "outward").
        return d <= opp or d >= -opp

    for f in bm.faces:
        n = f.normal
        if n.length_squared < 1e-12:
            continue
        n = n.normalized()
        c = f.calc_center_median()
        # Só sanduíche fino: +N bate na gémea dentro de wall_gap.
        # Hit longo (oco/torre) NÃO remove — protege sino e interiores.
        if _thin_twin(c + n * eps, n, n):
            doomed.append(f)

    if not doomed:
        bm.free()
        return 0

    total = len(bm.faces)
    max_remove = max(1, int(float(max_removal_ratio) * total))
    if len(doomed) > max_remove:
        log.warning(
            "remove_internal_shell_faces: abort %d/%d faces (max_ratio=%.2f)",
            len(doomed),
            total,
            max_removal_ratio,
        )
        bm.free()
        return 0

    removed = len(doomed)
    bmesh.ops.delete(bm, geom=doomed, context="FACES")
    orphans = [v for v in bm.verts if not v.link_faces]
    if orphans:
        bmesh.ops.delete(bm, geom=orphans, context="VERTS")
    bm.to_mesh(me)
    me.update()
    bm.free()
    if removed:
        log.info(
            "remove_internal_shell_faces: %d faces (wall_gap=%.4f diag=%.4f)",
            removed,
            wall_gap,
            diag,
        )
    return removed


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


def loop_is_cappable(
    loop: list[Any],
    *,
    max_edges: int,
    planar_tol: float,
    max_diameter: float | None = None,
) -> bool:
    """Loop pequeno e ~planar → seguro tapar. Cortes gigantes/serpenteantes → não.

    ``max_diameter``: se dado, loops com diâmetro ≥ este valor (ex. porta/janela)
    não são tapados mesmo que tenham poucas arestas densas.
    """
    if len(loop) < 3 or len(loop) > max_edges:
        return False
    stats = _loop_plane_stats(loop)
    if stats is None:
        return False
    _center, _normal, thickness, diameter = stats
    if max_diameter is not None and diameter >= float(max_diameter):
        return False
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
    max_loop_diameter_ratio: float | None = None,
) -> int:
    """Fecha boundary loops seguros de um bpy mesh object (in-place).

    Tapar TODOS os loops (``fill_holes(sides=0)``) cria membranas gigantes
    sobre cortes não-planares (fachada inteira tapada por um "sólido"). Aqui
    só se tapam loops com ≤ ``max_loop_edges`` arestas e aproximadamente
    planares (desvio RMS < ``planar_tol`` x diâmetro). Com ``cap_base=True``,
    loops planares horizontais na banda inferior da bbox (fundos abertos de
    edifícios) são tapados sem limite de arestas.

    ``max_loop_diameter_ratio``: fracção da diagonal AABB — loops maiores
    (portas/janelas) nunca são tapados, excepto bases com ``cap_base``.

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
    max_diameter: float | None = None
    if bm.verts:
        coords = np.array([v.co[:] for v in bm.verts], dtype=np.float64)
        if world_matrix is not None:
            coords = (np.c_[coords, np.ones(len(coords))] @ world_matrix.T)[:, :3]
        bbox_min, bbox_max = coords.min(axis=0), coords.max(axis=0)
        if max_loop_diameter_ratio is not None and max_loop_diameter_ratio > 0:
            diag = float(np.linalg.norm(bbox_max - bbox_min)) or 1.0
            max_diameter = float(max_loop_diameter_ratio) * diag
    if not cap_base:
        bbox_min = bbox_max = None

    capped = 0
    for loop in boundary_loops(bm):
        ok = loop_is_cappable(loop, max_edges=max_loop_edges, planar_tol=planar_tol, max_diameter=max_diameter)
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
    skip_flap_erode: bool = False,
    max_loop_diameter_ratio: float | None = None,
) -> dict[str, int]:
    """Fecha uma mesh até watertight (só bpy/bmesh), do seguro para o bruto.

    Camadas: (1) caps planares seletivos + base aberta; (2) ``fill_holes``
    progressivo (12 → 64 lados) para rachas; (3) ``final_fill``: fill_holes
    sem limite nos loops restantes — garante fecho, ao custo de membranas em
    loops não-planares residuais (raros depois das camadas anteriores).

    Folhas quase-planares (bbox 2D) saltam o fecho agressivo — não têm volume
    para fechar e o pinch/erode pode apagar todas as faces.

    ``skip_flap_erode``: não erode abas de fronteira (preserva escadas/bandeiras
    finas coladas ao volume; pode deixar boundary residual).

    ``max_loop_diameter_ratio``: evita tapar portas/janelas (loops grandes).

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
        obj,
        max_loop_edges=max_loop_edges,
        planar_tol=planar_tol,
        cap_base=cap_base,
        max_loop_diameter_ratio=max_loop_diameter_ratio,
    )
    # Limitar fill_holes ao mesmo teto de arestas dos caps — senão sides=64
    # tapa janelas pequenas mesmo com final_fill=False.
    fill_sides = tuple(s for s in (12, 32, 64) if s <= int(max_loop_edges))
    if not fill_sides and max_loop_edges > 0:
        fill_sides = (int(max_loop_edges),)
    for sides in fill_sides:
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
        if count_boundary_edges(obj) > 0 and not skip_flap_erode:
            # Bordos de abas de parede interna (MC) não são weldáveis nem
            # tapáveis: erodir as abas até à junção manifold e re-tapar.
            stats["flap_faces_eroded"] = _erode_boundary_flaps(obj)
            with_suppress_fill(obj, 0)
            _cap_all_remaining_loops(obj)
        elif count_boundary_edges(obj) > 0 and skip_flap_erode:
            stats["flap_erode_skipped"] = 1
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


def infer_up_axis(coords: np.ndarray) -> int:
    """Eixo vertical em coords locais = maior extensão AABB (clamp flare / local)."""
    if coords.size == 0:
        return 1
    ext = coords.max(axis=0) - coords.min(axis=0)
    return int(np.argmax(ext))


def infer_world_up_axis(obj: Any) -> int:
    """Eixo vertical em espaço mundo (pós-glTF costuma ser Z, não Y local).

    Preferência: soleira perto de 0 (``export-origin feet``) x extensão grande.
    Evita usar profundidade (Y mundo após conversão glTF) como “altura”.
    """
    mw = np.array(obj.matrix_world, dtype=np.float64)
    coords = np.array([mw @ np.array([*v.co, 1.0], dtype=np.float64) for v in obj.data.vertices], dtype=np.float64)[
        :, :3
    ]
    if len(coords) == 0:
        return 1
    bb_min = coords.min(axis=0)
    bb_max = coords.max(axis=0)
    ext = bb_max - bb_min
    best_ax, best_score = 1, -1.0
    for ax in (0, 1, 2):
        e = float(ext[ax])
        if e < 1e-9:
            continue
        # Soleira perto de 0 → score alto; eixo “deitado” com min≪0 perde.
        sole = abs(float(bb_min[ax]))
        score = e / (sole + 0.05 * float(ext.max()) + 1e-9)
        if score > best_score:
            best_score = score
            best_ax = ax
    return int(best_ax)


def base_openness_stats(
    obj: Any,
    *,
    up_axis: int | None = None,
    band: float = 0.10,
    grid: int = 48,
) -> dict[str, float]:
    """Mede oco por baixo via raios no footprint (espaço mundo).

    ``up_axis=None`` → :func:`infer_world_up_axis` (glTF Y-up → Z mundo).

    Returns:
        ``interior_cells``, ``recess_cells``, ``recess_ratio``, ``height``,
        ``plane_y`` (cota sugerida para tampa flush nas soleiras), ``up_axis``.
    """
    import bmesh
    from mathutils.bvhtree import BVHTree

    empty = {
        "interior_cells": 0.0,
        "recess_cells": 0.0,
        "recess_ratio": 0.0,
        "height": 0.0,
        "plane_y": 0.0,
        "up_axis": 1.0,
    }
    if len(obj.data.polygons) == 0:
        return empty
    if up_axis is None:
        up_axis = infer_world_up_axis(obj)
    if up_axis not in (0, 1, 2):
        return empty
    empty["up_axis"] = float(up_axis)

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.transform(obj.matrix_world)
    bm.faces.ensure_lookup_table()
    coords = np.array([v.co[:] for v in bm.verts], dtype=np.float64)
    if len(coords) == 0:
        bm.free()
        return empty

    bb_min, bb_max = coords.min(axis=0), coords.max(axis=0)
    height = float(bb_max[up_axis] - bb_min[up_axis]) or 1.0
    y0 = float(bb_min[up_axis])
    h_axes = [i for i in (0, 1, 2) if i != up_axis]
    ax, cx = h_axes[0], h_axes[1]
    amin, amax = float(bb_min[ax]), float(bb_max[ax])
    cmin, cmax = float(bb_min[cx]), float(bb_max[cx])
    if amax - amin < 1e-9 or cmax - cmin < 1e-9:
        bm.free()
        return {**empty, "height": height, "plane_y": y0}

    bvh = BVHTree.FromBMesh(bm)
    n = max(8, int(grid))
    interior = recess = 0
    solid_ys: list[float] = []
    direction = [0.0, 0.0, 0.0]
    direction[up_axis] = 1.0
    dir_t = tuple(direction)
    for ix in range(n):
        for iz in range(n):
            origin = [0.0, 0.0, 0.0]
            origin[ax] = amin + (ix + 0.5) / n * (amax - amin)
            origin[cx] = cmin + (iz + 0.5) / n * (cmax - cmin)
            origin[up_axis] = y0 - 0.02 * height
            hit = bvh.ray_cast(tuple(origin), dir_t, height * 1.25)
            if hit[0] is None:
                continue
            interior += 1
            hy = float(hit[0][up_axis])
            if hy > y0 + band * height:
                recess += 1
            else:
                solid_ys.append(hy)
    bm.free()
    ratio = float(recess) / float(interior) if interior else 0.0
    # Soleira: mediana dos hits “chão” (sólidos). Fallback: quase ymin.
    if solid_ys:
        plane_y = float(np.median(solid_ys))
    else:
        bot = coords[coords[:, up_axis] <= y0 + band * height, up_axis]
        plane_y = float(np.percentile(bot, 85)) if len(bot) else y0
    # Nunca acima da banda — evita cortar o corpo; nunca muito abaixo (pedestal).
    plane_y = float(np.clip(plane_y, y0, y0 + band * height))
    return {
        "interior_cells": float(interior),
        "recess_cells": float(recess),
        "recess_ratio": ratio,
        "height": height,
        "plane_y": plane_y,
        "up_axis": float(up_axis),
    }


def force_close_base(
    obj: Any,
    *,
    up_axis: int | None = None,
    band: float = 0.10,
    recess_trigger: float = 0.50,
    grid: int = 64,
    min_cells: int = 8,
    min_faces: int = 1000,
) -> dict[str, int]:
    """Fecha base oca com grelha planar flush — sem pedestal / volume.

    Shells manifold sem chão (Hunyuan) não têm boundary loops: bisect+fill
    só tapa o anel da parede e o oco continua. Esta rotina:

      1. Mede oco por raios no eixo vertical mundo (glTF → Z).
      2. Se ``recess_ratio`` ≥ trigger, gera chão planar flush no fundo real
         **só nas células ocas** totalmente dentro do footprint.
      3. Solda o rebordo do chão aos verts de parede próximos.

    Alvo: meshes marching-cubes densas (o weld do rebordo precisa de verts de
    parede próximos) — ``min_faces`` salta low-poly, onde o rebordo ficaria
    solto e o ``make_watertight`` normal já resolve.
    """
    import bmesh
    from mathutils.bvhtree import BVHTree

    stats: dict[str, int] = {"base_forced_faces": 0, "base_forced_cells": 0}
    if len(obj.data.polygons) < max(1, int(min_faces)):
        return stats
    if up_axis is None:
        up_axis = infer_world_up_axis(obj)
    if up_axis not in (0, 1, 2):
        return stats

    openness = base_openness_stats(obj, up_axis=up_axis, band=band, grid=grid)
    stats["base_recess_pct"] = round(100.0 * float(openness["recess_ratio"]))
    stats["base_up_axis"] = int(up_axis)
    if float(openness["interior_cells"]) < min_cells:
        return stats
    if float(openness["recess_ratio"]) < recess_trigger:
        return stats

    height = float(openness["height"]) or 1.0

    # Re-ray no footprint: só células recess recebem quads (chão).
    bm_w = bmesh.new()
    bm_w.from_mesh(obj.data)
    bm_w.transform(obj.matrix_world)
    bm_w.faces.ensure_lookup_table()
    coords = np.array([v.co[:] for v in bm_w.verts], dtype=np.float64)
    bb_min, bb_max = coords.min(axis=0), coords.max(axis=0)
    y0 = float(bb_min[up_axis])
    # Chão flush no fundo real do modelo — não na cota das soleiras (a meio
    # das paredes o plano ficava visível acima do rebordo inferior).
    plane_y_world = y0 + 0.002 * height
    h_axes = [i for i in (0, 1, 2) if i != up_axis]
    ax, cx = h_axes[0], h_axes[1]
    amin, amax = float(bb_min[ax]), float(bb_max[ax])
    cmin, cmax = float(bb_min[cx]), float(bb_max[cx])
    bvh = BVHTree.FromBMesh(bm_w)
    n = max(8, int(grid))
    direction = [0.0, 0.0, 0.0]
    direction[up_axis] = 1.0
    dir_t = tuple(direction)

    def _ray_hit_y(a: float, c: float) -> float | None:
        origin = [0.0, 0.0, 0.0]
        origin[ax] = a
        origin[cx] = c
        origin[up_axis] = y0 - 0.02 * height
        hit = bvh.ray_cast(tuple(origin), dir_t, height * 1.25)
        return float(hit[0][up_axis]) if hit[0] is not None else None

    # Cantos partilhados: cache (n+1)² testes de interioridade do footprint.
    corner_inside: dict[tuple[int, int], bool] = {}

    def _corner_ok(ix: int, iz: int) -> bool:
        key = (ix, iz)
        if key not in corner_inside:
            a = amin + ix / n * (amax - amin)
            c = cmin + iz / n * (cmax - cmin)
            corner_inside[key] = _ray_hit_y(a, c) is not None
        return corner_inside[key]

    recess_cells: list[tuple[int, int]] = []
    for ix in range(n):
        for iz in range(n):
            hy = _ray_hit_y(
                amin + (ix + 0.5) / n * (amax - amin),
                cmin + (iz + 0.5) / n * (cmax - cmin),
            )
            if hy is None or hy <= y0 + band * height:
                continue
            # Só células totalmente dentro do footprint — cantos fora da
            # parede criavam abas salientes para fora do modelo.
            if all(_corner_ok(ix + dx, iz + dz) for dx in (0, 1) for dz in (0, 1)):
                recess_cells.append((ix, iz))
    bm_w.free()
    if len(recess_cells) < min_cells:
        return stats

    mw = np.array(obj.matrix_world, dtype=np.float64)
    try:
        inv = np.linalg.inv(mw)
    except np.linalg.LinAlgError:
        log.warning("force_close_base: matrix_world não invertível")
        return stats

    def world_to_local(pw: np.ndarray) -> tuple[float, float, float]:
        pl = (inv @ np.array([pw[0], pw[1], pw[2], 1.0], dtype=np.float64))[:3]
        return float(pl[0]), float(pl[1]), float(pl[2])

    da = (amax - amin) / n
    dc = (cmax - cmin) / n
    cell = max(da, dc)
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    new_verts: list[Any] = []
    new_faces = 0
    # Cantos partilhados entre células: um vert por canto (grelha soldada).
    vert_cache: dict[tuple[int, int], Any] = {}

    def _grid_vert(gx: int, gz: int) -> Any:
        key = (gx, gz)
        v = vert_cache.get(key)
        if v is None:
            pw = np.zeros(3, dtype=np.float64)
            pw[ax] = amin + gx * da
            pw[cx] = cmin + gz * dc
            pw[up_axis] = plane_y_world
            v = bm.verts.new(world_to_local(pw))
            vert_cache[key] = v
            new_verts.append(v)
        return v

    for ix, iz in recess_cells:
        verts = [_grid_vert(ix, iz), _grid_vert(ix + 1, iz), _grid_vert(ix + 1, iz + 1), _grid_vert(ix, iz + 1)]
        try:
            bm.faces.new(verts)
            new_faces += 1
        except ValueError:
            continue
    if new_faces == 0:
        bm.free()
        return stats
    # Soldar o rebordo do chão às paredes: snap de cada vert do perímetro ao
    # vert de parede mais próximo. Sem remove_doubles em massa — colapsaria o
    # detalhe denso da base das paredes.
    try:
        from mathutils.kdtree import KDTree

        new_set = set(new_verts)
        perimeter = [v for v in new_verts if any(len(e.link_faces) < 2 for e in v.link_edges)]
        row = mw[up_axis, :]
        old_verts = [v for v in bm.verts if v not in new_set]
        if perimeter and old_verts:
            heights = np.array(
                [row[0] * v.co[0] + row[1] * v.co[1] + row[2] * v.co[2] + row[3] for v in old_verts],
                dtype=np.float64,
            )
            near_idx = np.nonzero(np.abs(heights - plane_y_world) <= 3.0 * cell)[0]
            wall_near = [old_verts[i] for i in near_idx]
            if wall_near:
                kd = KDTree(len(wall_near))
                for i, v in enumerate(wall_near):
                    kd.insert(v.co, i)
                kd.balance()
                weld: set[Any] = set()
                for v in perimeter:
                    co, idx, dist = kd.find(v.co)
                    if idx is not None and dist <= 3.0 * cell:
                        v.co = co
                        weld.add(v)
                        weld.add(wall_near[idx])
                if weld:
                    bmesh.ops.remove_doubles(bm, verts=list(weld), dist=1e-6)
    except Exception as exc:
        log.warning("force_close_base: weld chão↔parede falhou: %s", exc)
    try:
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
    except Exception as exc:
        log.warning("force_close_base: triangulate/normals falhou: %s", exc)
    bm.to_mesh(obj.data)
    obj.data.update()
    bm.free()

    stats["base_forced_faces"] = int(new_faces)
    stats["base_forced_cells"] = len(recess_cells)
    try:
        normals_consistent(obj)
    except Exception as exc:
        log.warning("normals pós-force_close_base falhou: %s", exc)
    log.info(
        "force_close_base: floor planar faces=%d cells=%d recess=%d%% plane=%.4f up=%d",
        stats["base_forced_faces"],
        stats["base_forced_cells"],
        stats["base_recess_pct"],
        plane_y_world,
        up_axis,
    )
    return stats


def clamp_base_flare(
    obj: Any,
    *,
    up_axis: int | None = None,
    bottom_frac: float = 0.12,
    ref_lo_frac: float = 0.20,
    ref_hi_frac: float = 0.45,
    max_flare_ratio: float = 1.06,
    ref_percentile: float = 95.0,
) -> int:
    """Puxa verts da base cujo raio radial > ``max_flare_ratio`` x raio ref.

    Heurística geral (sem regras de asset): Hunyuan/MC muitas vezes engorda
    o contacto com o chão (“pés de elefante”). O raio de referência vem do
    percentil dos verts a mid-height; só a faixa inferior é corrigida, com
    falloff smoothstep (evita “saia”/lip no corte superior).

    Args:
        obj: bpy mesh object.
        up_axis: Eixo vertical local; ``None`` = eixo AABB mais longo.
        bottom_frac: Fração inferior da altura a corrigir.
        ref_lo_frac / ref_hi_frac: Banda de altura para o raio de referência.
        max_flare_ratio: Acima disto, o vert é puxado radialmente.
        ref_percentile: Percentil do raio na banda de referência.

    Returns:
        Número de vértices deslocados.
    """
    import bmesh

    from gamedev_shared.bpy_mesh import _require_bpy

    _require_bpy()
    if obj is None or getattr(obj, "type", None) != "MESH":
        return 0
    if len(obj.data.vertices) == 0:
        return 0

    mesh = obj.data
    coords = np.array([v.co[:] for v in mesh.vertices], dtype=np.float64)
    if up_axis is None:
        up_axis = infer_up_axis(coords)
    lo = coords[:, up_axis].min()
    hi = coords[:, up_axis].max()
    height = float(hi - lo)
    if height < 1e-8:
        return 0

    axes = [i for i in range(3) if i != up_axis]
    bottom_cut = lo + bottom_frac * height
    ref_lo = lo + ref_lo_frac * height
    ref_hi = lo + ref_hi_frac * height
    mid = (coords[:, up_axis] >= ref_lo) & (coords[:, up_axis] <= ref_hi)
    if int(mid.sum()) < 8:
        mid = (coords[:, up_axis] >= lo + 0.15 * height) & (coords[:, up_axis] <= lo + 0.55 * height)
    if int(mid.sum()) < 4:
        # Último recurso: todos os verts excepto a faixa inferior (evita
        # percentil vazio em malhas só com anéis nas pontas).
        mid = coords[:, up_axis] > bottom_cut
    if int(mid.sum()) < 4:
        return 0

    center = coords[mid][:, axes].mean(axis=0)
    mid_r = np.linalg.norm(coords[mid][:, axes] - center, axis=1)
    if mid_r.size == 0:
        return 0
    ref_r = float(np.percentile(mid_r, ref_percentile))
    if ref_r < 1e-6:
        return 0
    limit = ref_r * float(max_flare_ratio)

    bot = coords[:, up_axis] <= bottom_cut
    bot_xy = coords[bot][:, axes]
    bot_r = np.linalg.norm(bot_xy - center, axis=1)
    over = bot_r > limit
    if not over.any():
        return 0

    # Falloff vertical: 1 no chão → 0 em bottom_cut (smoothstep).
    ys = coords[bot][:, up_axis]
    t = np.clip((ys - lo) / max(bottom_cut - lo, 1e-12), 0.0, 1.0)
    weight = 1.0 - t
    weight = weight * weight * (3.0 - 2.0 * weight)

    # Escala radial com blend (mantém ângulo).
    scale = np.ones(bot_r.shape[0], dtype=np.float64)
    target = limit / np.maximum(bot_r[over], 1e-12)
    scale[over] = 1.0 + (target - 1.0) * weight[over]
    new_xy = center + (bot_xy - center) * scale[:, None]

    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.verts.ensure_lookup_table()
    bot_idx = np.flatnonzero(bot)
    moved = 0
    for local_i, vi in enumerate(bot_idx):
        if not over[local_i] or weight[local_i] < 1e-4:
            continue
        v = bm.verts[int(vi)]
        co = list(v.co)
        co[axes[0]] = float(new_xy[local_i, 0])
        co[axes[1]] = float(new_xy[local_i, 1])
        v.co = co
        moved += 1
    if moved:
        bm.to_mesh(mesh)
        mesh.update()
    bm.free()
    if moved:
        log.info(
            "clamp_base_flare: %d verts (ref_r=%.4f limit=%.4f up=%d)",
            moved,
            ref_r,
            limit,
            up_axis,
        )
    return moved


def taubin_smooth(
    obj: Any,
    *,
    iterations: int = 3,
    lam: float = 0.5,
    mu: float = -0.53,
) -> int:
    """Suavização Taubin (volume-preserving) via laplaciano de vizinhança.

    Reduz ruído “argila” do marching cubes sem o colapso de finos típico
    de remesh isotrópico agressivo.

    Returns:
        Número de iterações aplicadas (0 se skip).
    """
    import bmesh

    from gamedev_shared.bpy_mesh import _require_bpy

    _require_bpy()
    if obj is None or getattr(obj, "type", None) != "MESH":
        return 0
    n_verts = len(obj.data.vertices)
    if n_verts < 8 or iterations <= 0:
        return 0

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()

    def _laplacian_step(factor: float) -> None:
        # Δ = avg(neighbors) - v
        deltas = [None] * len(bm.verts)
        for v in bm.verts:
            linked = v.link_edges
            if not linked:
                deltas[v.index] = (0.0, 0.0, 0.0)
                continue
            acc = [0.0, 0.0, 0.0]
            for e in linked:
                other = e.other_vert(v)
                acc[0] += other.co.x
                acc[1] += other.co.y
                acc[2] += other.co.z
            n = float(len(linked))
            deltas[v.index] = (
                acc[0] / n - v.co.x,
                acc[1] / n - v.co.y,
                acc[2] / n - v.co.z,
            )
        for v in bm.verts:
            d = deltas[v.index]
            v.co.x += factor * d[0]
            v.co.y += factor * d[1]
            v.co.z += factor * d[2]

    applied = 0
    for _ in range(int(iterations)):
        _laplacian_step(float(lam))
        _laplacian_step(float(mu))
        applied += 1

    bm.to_mesh(obj.data)
    obj.data.update()
    bm.free()
    if applied:
        log.info("taubin_smooth: %d iterações (λ=%.2f μ=%.2f)", applied, lam, mu)
    return applied


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
    # Cascas internas (paredes duplas MC) — antes de fill/watertight.
    do_remove_internal_shells: bool = False
    internal_shell_wall_gap_ratio: float = 0.08
    internal_shell_room_gap_ratio: float = 0.55  # deprecated no-op
    internal_shell_max_removal_ratio: float = 0.55
    internal_shell_passes: int = 2
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
    # Fracção da diagonal AABB — loops ≥ isto não são tapados (portas/janelas).
    watertight_max_loop_diameter_ratio: float | None = None
    watertight_skip_flap_erode: bool = False
    # Bisect+fill se oco (capela ~0.75 no eixo mundo certo). Torre sólida ~0.01.
    force_close_base: bool = False
    force_base_band: float = 0.10
    force_base_recess_trigger: float = 0.50
    force_base_grid: int = 64
    recalc_normals: bool = True
    # Pré-passo: colapsar normal-splits do glTF (topology-fix em GLB).
    do_reweld_coincident: bool = False
    reweld_threshold: float = 1e-6
    # Pós-fecho: anti “pés de elefante” + suavização volume-preserving.
    do_clamp_base_flare: bool = False
    flare_max_ratio: float = 1.06
    flare_bottom_frac: float = 0.10
    do_taubin: bool = False
    taubin_iterations: int = 3
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
        # Lean: weld + debris/slivers + micro fill. Sem cascas internas,
        # watertight agressivo, force/cap base, flare ou Taubin — esses
        # passos destruíam edifícios tipo casca-plástico (capela) ou props
        # interiores. Double-shell / fundo aberto → geração (prompt/vista).
        do_remove_internal_shells=False,
        fill_holes_sides=32,
        watertight=False,
        watertight_cap_base=False,
        watertight_final_fill=False,
        force_close_base=False,
        do_clamp_base_flare=False,
        do_taubin=False,
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
        do_remove_internal_shells=prof.do_remove_internal_shells,
        internal_shell_wall_gap_ratio=prof.internal_shell_wall_gap_ratio,
        internal_shell_room_gap_ratio=prof.internal_shell_room_gap_ratio,
        internal_shell_max_removal_ratio=prof.internal_shell_max_removal_ratio,
        internal_shell_passes=prof.internal_shell_passes,
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
        watertight_max_loop_diameter_ratio=prof.watertight_max_loop_diameter_ratio,
        watertight_skip_flap_erode=prof.watertight_skip_flap_erode,
        do_force_close_base=prof.force_close_base,
        force_base_band=prof.force_base_band,
        force_base_recess_trigger=prof.force_base_recess_trigger,
        force_base_grid=prof.force_base_grid,
        recalc_normals=prof.recalc_normals,
        do_clamp_base_flare=prof.do_clamp_base_flare,
        flare_max_ratio=prof.flare_max_ratio,
        flare_bottom_frac=prof.flare_bottom_frac,
        do_taubin=prof.do_taubin,
        taubin_iterations=prof.taubin_iterations,
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
    do_remove_internal_shells: bool = False,
    internal_shell_wall_gap_ratio: float = 0.12,
    internal_shell_room_gap_ratio: float = 0.55,
    internal_shell_max_removal_ratio: float = 0.55,
    internal_shell_passes: int = 2,
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
    watertight_max_loop_diameter_ratio: float | None = None,
    watertight_skip_flap_erode: bool = False,
    do_force_close_base: bool = False,
    force_base_band: float = 0.10,
    force_base_recess_trigger: float = 0.50,
    force_base_grid: int = 64,
    recalc_normals: bool = True,
    do_clamp_base_flare: bool = False,
    flare_max_ratio: float = 1.06,
    flare_bottom_frac: float = 0.10,
    do_taubin: bool = False,
    taubin_iterations: int = 3,
) -> dict[str, int]:
    """Sequência completa de reparação in-place num bpy mesh object.

    Preserva UVs, materiais, vertex groups (skin weights) e não toca em
    armatures/animações. Objetos com shape keys só recebem passos não
    destrutivos (welds/deletes corromperiam os morph targets).

    Ordem: NaN guard → weld exacto → weld secundário (bbox / densidade /
    fixed) → degenerate → loose → long edges → slivers → debris →
    internal shells (opt) → fill holes → cap loops (opt) → ``make_watertight``
    (opt) → ``force_close_base`` (opt) → clamp flare → Taubin → normais.

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
    if do_remove_internal_shells:
        total_shell = 0
        passes = max(1, int(internal_shell_passes))
        try:
            for _pass in range(passes):
                n = remove_internal_shell_faces(
                    obj,
                    wall_gap_ratio=internal_shell_wall_gap_ratio,
                    room_gap_ratio=internal_shell_room_gap_ratio,
                    max_removal_ratio=internal_shell_max_removal_ratio,
                )
                total_shell += int(n)
                if n <= 0:
                    break
        except Exception as exc:
            log.warning("remove_internal_shell_faces falhou: %s", exc)
        stats["internal_shell_faces"] = total_shell
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
                skip_flap_erode=watertight_skip_flap_erode,
                max_loop_diameter_ratio=watertight_max_loop_diameter_ratio,
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
            if wt.get("flap_erode_skipped"):
                stats["flap_erode_skipped"] = int(wt["flap_erode_skipped"])
        except Exception as exc:
            log.warning("make_watertight falhou: %s", exc)
            stats["boundary_after"] = count_boundary_edges(obj)
    if do_clamp_base_flare:
        try:
            stats["flare_verts_clamped"] = clamp_base_flare(
                obj,
                bottom_frac=flare_bottom_frac,
                max_flare_ratio=flare_max_ratio,
            )
        except Exception as exc:
            log.warning("clamp_base_flare falhou: %s", exc)
            stats["flare_verts_clamped"] = 0
    if do_taubin and taubin_iterations > 0:
        try:
            stats["taubin_iters"] = taubin_smooth(obj, iterations=taubin_iterations)
        except Exception as exc:
            log.warning("taubin_smooth falhou: %s", exc)
            stats["taubin_iters"] = 0
    # Chão planar DEPOIS do Taubin — senão o smooth empena a grelha e o oco volta.
    # Param ``do_force_close_base`` (não ``force_close_base``) evita shadow da função.
    if do_force_close_base:
        try:
            fb = force_close_base(
                obj,
                band=force_base_band,
                recess_trigger=force_base_recess_trigger,
                grid=force_base_grid,
            )
            stats.update({k: int(v) for k, v in fb.items()})
        except Exception as exc:
            log.warning("force_close_base falhou: %s", exc)
            stats["base_forced_faces"] = 0
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
