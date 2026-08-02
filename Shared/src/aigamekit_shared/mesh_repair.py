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
* ``clamp_base_flare`` — puxa “pés de elefante” MC (base mais larga que o
  corpo) para o raio de referência do meio do mesh (opt-in; **off** em
  ``topology_clean``).
* ``taubin_smooth`` — suavização volume-preserving (reduz argila MC sem
  derreter finos como um remesh; **off** em ``topology_clean``).
* ``force_close_base`` — **removido**. Não reintroduzir: bisect/chão planar
  destruía edifícios casca-plástico. Base oca por baixo é aceitável em jogo
  (câmara não olha para baixo). Ver
  ``docs/HUNYUAN_MESH_AND_PARTS_LESSONS_PT.md``.
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

from aigamekit_shared.bpy_mesh import (
    clear_scene,
    create_mesh_from_arrays,
    save_glb,
    tri_count,
    vertex_coords,
)

log = logging.getLogger(__name__)

WeldMode = Literal["bbox", "vert_density", "fixed", "none"]
RepairProfileName = Literal["topology_clean", "pre_decimate_uv", "part_decode", "post_voxel"]

# Cap default da grelha do voxel remesh (por eixo) em ``morphological_close``.
# A resolução auto de morph-close (Text3D/bbox_tune) usa-o para não propor
# distâncias que a grelha não consegue resolver (fecho sub-voxel = no-op caro).
MORPH_MAX_GRID_AXIS = 800
MORPH_MIN_GRID_AXIS = 64
# OpenVDB + overhead bpy por célula (cubo denso, pior caso). 800³x48 ≈ 23 GiB
# teóricos — sem adaptação o topology-fix em edifícios MC mata o processo.
MORPH_BYTES_PER_GRID_CELL = 48
# Fração da RAM *disponível* reservada ao volume; resto = solidify/cópias/OS.
MORPH_RAM_FRACTION = 0.20
# Solidify duplica topologia antes do remesh — input HI (1-4M faces) é OOM
# mesmo com grelha já clamped. Morph remesha à resolução do voxel; faces
# acima deste tecto não ajudam o fecho.
MORPH_INPUT_FACE_CAP = 400_000


def available_ram_bytes() -> int | None:
    """RAM disponível do sistema (``MemAvailable`` / psutil). ``None`` se unknown."""
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    parts = line.split()
                    if len(parts) >= 2:
                        return int(parts[1]) * 1024
    except (OSError, ValueError):
        pass
    try:
        import psutil

        return int(psutil.virtual_memory().available)
    except Exception:
        return None


def adapt_morph_max_grid_axis(
    available_bytes: int | None,
    *,
    requested: int = MORPH_MAX_GRID_AXIS,
    bytes_per_cell: int = MORPH_BYTES_PER_GRID_CELL,
    fraction: float = MORPH_RAM_FRACTION,
    min_axis: int = MORPH_MIN_GRID_AXIS,
) -> int:
    """Cap de grelha morph que cabe na RAM disponível (anti-OOM).

    Premissa do monorepo: OOM não é tolerado — o pipeline desce resolução
    sozinho. Mesma ideia que :func:`aigamekit_shared.vram_budget.budget_units`
    para activação GPU, mas para o cubo OpenVDB do voxel remesh.

    Sem sinal de RAM: tecto conservador 400 (histórico OOM em buildings ~10 m
    com grelha 800).
    """
    req = max(int(min_axis), int(requested))
    lo = max(8, int(min_axis))
    if available_bytes is None or available_bytes <= 0:
        return min(req, 400)
    bpp = max(1, int(bytes_per_cell))
    frac = min(1.0, max(0.01, float(fraction)))
    max_cells = int(available_bytes * frac) // bpp
    if max_cells < 1:
        return lo
    grid = int(max_cells ** (1.0 / 3.0))
    return max(lo, min(req, grid))


def morph_input_face_cap(max_grid_axis: int) -> int:
    """Tecto de faces *antes* do solidify/voxel — escala com a grelha efectiva.

    Superfície ~O(G²); manter mais faces que o remesh resolve só inflaciona
    o solidify (x2) sem melhorar o fecho.
    """
    g = max(MORPH_MIN_GRID_AXIS, int(max_grid_axis))
    surface = 4 * g * g
    return int(min(MORPH_INPUT_FACE_CAP, max(80_000, surface)))


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
    collapse: bool = True,
    short_edge_factor: float = 2.0,
) -> int:
    """Elimina triângulos-agulha (aspecto = longest_edge² / area).

    Típico em sparks/fuses/folhas finas após decimação agressiva: faces com
    área ~0 e arestas longas viram "estrelas" visuais. Aborta se afectaria
    mais de ``max_removal_ratio`` das faces (malha já quase só slivers).

    Com ``collapse=True`` (default) a agulha é removida por **edge collapse**
    da sua aresta mais curta (``bmesh.ops.collapse``, UVs interpoladas): numa
    malha fechada isso apaga as faces adjacentes sem abrir buraco. O delete
    histórico (``collapse=False``) abre um buraco por agulha — em meshes que
    entram watertight o ``fill_holes`` a seguir tapa-os com leques n-gon
    visíveis (sintoma "faces em falta").

    Só arestas curtas (``<= short_edge_factor x mediana``) são colapsadas;
    agulhas "cap" (obtusas, três arestas longas) ficam intactas em vez de
    deformar a silhueta.

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

    n_faces_before = len(bm.faces)
    if collapse:
        bm.edges.ensure_lookup_table()
        lengths = [e.calc_length() for e in bm.edges]
        median = float(sorted(lengths)[len(lengths) // 2]) if lengths else 0.0
        max_len = short_edge_factor * median if median > 0 else float("inf")
        short_edges = {
            e for f in doomed for e in [min(f.edges, key=lambda x: x.calc_length())] if e.calc_length() <= max_len
        }
        if not short_edges:
            bm.free()
            return 0
        bmesh.ops.collapse(bm, edges=list(short_edges), uvs=True)
    else:
        bmesh.ops.delete(bm, geom=doomed, context="FACES")
    orphans = [v for v in bm.verts if not v.link_faces]
    if orphans:
        bmesh.ops.delete(bm, geom=orphans, context="VERTS")
    bm.to_mesh(me)
    me.update()
    n = n_faces_before - len(me.polygons)
    bm.free()
    return int(max(0, n))


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
    world_matrix: np.ndarray | None = None,
) -> bool:
    """Loop pequeno e ~planar → seguro tapar. Cortes gigantes/serpenteantes → não.

    ``max_diameter``: se dado, loops com diâmetro ≥ este valor (ex. porta/janela)
    não são tapados mesmo que tenham poucas arestas densas.

    ``world_matrix`` tem de ser a mesma com que ``max_diameter`` foi calculado —
    o chamador deriva-o da diagonal da AABB em **mundo**, portanto medir o loop
    em espaço local compara escalas diferentes. Objectos importados de glTF com
    escala != 1 (ex. ``Top`` das árvores, escala 3.73) faziam o guarda de
    diâmetro nunca disparar e portas/janelas eram tapadas.
    """
    if len(loop) < 3 or len(loop) > max_edges:
        return False
    stats = _loop_plane_stats(loop, world_matrix)
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
        ok = loop_is_cappable(
            loop,
            max_edges=max_loop_edges,
            planar_tol=planar_tol,
            max_diameter=max_diameter,
            world_matrix=world_matrix,
        )
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
            # Contar só quando o fill produziu faces — holes_fill devolve um
            # dict vazio (sem excepção) em cadeias que não fecham, e o contador
            # antigo reportava loops "tapados" com a fronteira intacta.
            res = bmesh.ops.holes_fill(bm, edges=edges, sides=0)
            if res.get("faces"):
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


def count_boundary_edges_fast(obj: Any) -> int:
    """``count_boundary_edges`` via arrays (foreach_get + np.unique), sem bmesh.

    ~2x mais rápido por chamada em meshes grandes — usado nos loops de
    :func:`make_watertight`, que consultam a fronteira repetidamente.
    """
    me = obj.data
    if len(me.polygons) == 0:
        return 0
    me.calc_loop_triangles()
    m = len(me.loop_triangles)
    if m == 0:
        return 0
    tris_flat = np.empty(m * 3, dtype=np.int64)
    me.loop_triangles.foreach_get("vertices", tris_flat)
    tris: np.ndarray = tris_flat.reshape(-1, 3)
    e = np.empty((m * 3, 2), dtype=np.int64)
    e[0::3, 0] = tris[:, 0]
    e[0::3, 1] = tris[:, 1]
    e[1::3, 0] = tris[:, 1]
    e[1::3, 1] = tris[:, 2]
    e[2::3, 0] = tris[:, 2]
    e[2::3, 1] = tris[:, 0]
    e.sort(axis=1)
    keys = (e[:, 0] << 32) | e[:, 1]
    _, counts = np.unique(keys, return_counts=True)
    return int((counts == 1).sum())


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
    stats: dict[str, int] = {"boundary_before": count_boundary_edges_fast(obj)}
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
        if count_boundary_edges_fast(obj) == 0:
            break
        with_suppress_fill(obj, sides)

    if final_fill and not planar_sheet and count_boundary_edges_fast(obj) > 0:
        with_suppress_fill(obj, 0)
    if final_fill and not planar_sheet and count_boundary_edges_fast(obj) > 0:
        # fill_holes global falha em loops que tocam vértices non-manifold;
        # holes_fill do bmesh loop-a-loop é mais robusto como último recurso.
        stats["loops_forced"] = _cap_all_remaining_loops(obj)
    if final_fill and not planar_sheet:
        # Micro-rachas restantes são cadeias não-fechadas presas em junções
        # non-manifold (holes_fill exige loops fechados). Pinch dirigido:
        # weld localizado só nos verts de cada racha, depois re-fill.
        prev = count_boundary_edges_fast(obj)
        for _ in range(3):
            if prev == 0:
                break
            stats["cracks_pinched"] = stats.get("cracks_pinched", 0) + _pinch_small_boundary_chains(obj)
            with_suppress_fill(obj, 0)
            _cap_all_remaining_loops(obj)
            cur = count_boundary_edges_fast(obj)
            if cur >= prev:
                break
            prev = cur
        if count_boundary_edges_fast(obj) > 0 and not skip_flap_erode:
            # Bordos de abas de parede interna (MC) não são weldáveis nem
            # tapáveis: erodir as abas até à junção manifold e re-tapar.
            stats["flap_faces_eroded"] = _erode_boundary_flaps(obj)
            with_suppress_fill(obj, 0)
            _cap_all_remaining_loops(obj)
        elif count_boundary_edges_fast(obj) > 0 and skip_flap_erode:
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
    stats["boundary_after"] = count_boundary_edges_fast(obj)
    if stats["boundary_after"]:
        log.warning("make_watertight: %d arestas de fronteira restantes", stats["boundary_after"])
    return stats


# Volume abaixo do qual o guard do morph não se aplica (cascas/folhas abertas
# não têm volume para preservar).
_MORPH_MIN_VOLUME = 1e-6
# Fração do volume de entrada abaixo da qual o fecho morfológico é considerado
# degenerado (crosta do OpenVDB) e revertido. A margem medida é enorme: casos
# bons ficam em 1.00-1.03, os degenerados em 0.09.
_MORPH_MIN_VOLUME_RATIO = 0.5


def _signed_volume(obj: Any) -> float:
    """Volume assinado da malha via bmesh (unidades do objecto)."""
    import bmesh

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    v = float(bm.calc_volume(signed=True))
    bm.free()
    return v


def _restore_morph_backup(obj: Any, backup: Any, vol_before: float, vol_after: float) -> None:
    """Repõe a malha pré-morph e liberta o datablock temporário."""
    import bpy

    log.warning(
        "morphological_close: volume colapsou %.3f → %.3f (%.0f%%) — fecho revertido, malha original preservada",
        vol_before,
        vol_after,
        100.0 * vol_after / vol_before if vol_before else 0.0,
    )
    old = obj.data
    obj.data = backup
    bpy.data.meshes.remove(old)
    obj.data.update()


def _vertex_gap_weights(
    obj: Any,
    *,
    full_dist: float,
    zero_dist: float,
    cone_deg: float = 30.0,
) -> Any:
    """Peso por vértice = proximidade de parede oposta *à frente* (vão de racha).

    Lança um pequeno cone de raios ao longo da normal (para fora); a menor
    distância de impacto mede o vão. Interior de rachas/dobras: parede oposta
    muito próxima → peso 1. Superfícies expostas (sinos, colunas, arcos): sem
    impacto próximo → peso 0. Ao contrário de AO genérico, não marca zonas
    apenas semi-fechadas (interior de campanário), evitando fundir elementos
    separados por vãos abertos intencionais.

    Args:
        obj: Objeto mesh bpy.
        full_dist: Vão <= isto → peso 1.
        zero_dist: Vão >= isto → peso 0 (smoothstep entre os dois).
        cone_deg: Meio-ângulo do cone de raios à volta da normal.

    Returns:
        np.ndarray (n_verts,) de pesos 0..1.
    """
    import math as _math

    import bpy
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree

    deps = bpy.context.evaluated_depsgraph_get()
    bvh = BVHTree.FromObject(obj, deps)

    me = obj.data
    n_verts = len(me.vertices)
    gap = np.full(n_verts, np.inf, dtype=np.float64)
    eps = 1e-4
    tilt = _math.radians(cone_deg)
    sin_t, cos_t = _math.sin(tilt), _math.cos(tilt)
    for i, v in enumerate(me.vertices):
        nrm = v.normal
        if nrm.length == 0:
            continue
        n = nrm.normalized()
        origin = v.co + n * eps
        ref = Vector((1.0, 0.0, 0.0)) if abs(n.x) < 0.9 else Vector((0.0, 1.0, 0.0))
        t1 = n.cross(ref).normalized()
        t2 = n.cross(t1)
        dirs = [n]
        for a in (0.0, 0.5, 1.0, 1.5):
            ang = a * _math.pi
            dirs.append(n * cos_t + (t1 * _math.cos(ang) + t2 * _math.sin(ang)) * sin_t)
        best = np.inf
        for d in dirs:
            loc, _hn, _hi, hd = bvh.ray_cast(origin, d, zero_dist * 1.5)
            if loc is not None and hd < best:
                best = hd
        gap[i] = best

    # smoothstep invertido: vão pequeno → 1, vão grande → 0
    t = np.clip((gap - full_dist) / max(zero_dist - full_dist, 1e-6), 0.0, 1.0)
    t = t * t * (3.0 - 2.0 * t)
    return 1.0 - t


def morphological_close(
    obj: Any,
    *,
    distance: float = 0.10,
    voxel_size: float | None = None,
    wall_thickness: float | None = None,
    gap_mask: bool = False,
    gap_open_dist: float | None = None,
    max_grid_axis: int = 800,
) -> dict[str, int | float]:
    """Fecho morfológico volumétrico: solidify → voxel → dilate → voxel → erode → voxel.

    Resolve o caso "double shell fino": buracos de marching cubes que dobram
    para dentro ligando casca externa à interna — a mesh reporta boundary=0
    (topologicamente fechada) mas exibe rachas/buracos visíveis. Weld e
    fill_holes não os detetam porque não há arestas de fronteira.

    A dilatação (displace +``distance`` ao longo da normal) faz as duas
    margens da racha colidirem; o voxel remesh funde-as num sólido; a erosão
    (displace -``distance``) devolve a superfície à posição original já sem a
    racha. O solidify prévio garante paredes mínimas para não desaparecerem
    no voxel remesh — **escalado a ``distance``** (nunca um 8 cm fixo que
    derrete detalhe quando ``distance`` é milimétrico).

    Por defeito o fecho é uniforme (todos os vértices dilatam/erodem o mesmo)
    — simples e previsível; elementos finos muito próximos (< ~2*``distance``)
    podem fundir. ``gap_mask=True`` pondera pelo vão medido à frente de cada
    vértice (cone de raios ao longo da normal): rachas fecham a 100%, vãos
    abertos >= ``gap_open_dist`` não se movem — mas pode deixar rachas
    largas/irregulares por fechar (pesos parciais).

    ATENÇÃO: destrói UVs/materiais — usar apenas em meshes pré-paint
    (ex. ``topology-fix``). Fecha rachas com vão < ~2*``distance``;
    aberturas intencionais maiores sobrevivem.

    Args:
        obj: Objeto mesh bpy.
        distance: Raio do fecho em unidades de mundo (metros). Vãos menores
            que ~2x isto são fundidos.
        voxel_size: Tamanho do voxel do remesh. ``None`` → ``distance / 3``
            (piso ``min(0.01, distance/2)`` — não força 1 cm quando distance≪).
        wall_thickness: Espessura do solidify. ``None`` → ``2 * distance``
            (piso 2 mm). Valor fixo grande (ex. 0.08) engrossa tudo e anula
            ``distance`` pequeno.
        gap_mask: Ponderar o fecho pelo vão frontal (protege elementos abertos).
        gap_open_dist: Vão a partir do qual o peso é 0 (elemento "aberto").
            ``None`` → ``2.5 * distance``.
        max_grid_axis: Cap *pedido* de grelha por eixo (default 800). O valor
            efectivo desce automaticamente com :func:`adapt_morph_max_grid_axis`
            (RAM disponível) e o input é pré-decimado a
            :func:`morph_input_face_cap` — OOM do solidify/OpenVDB não é
            aceitável. Assets grandes com ``distance`` milimétrica batem no
            cap — fecho efectivo = escala da grelha (warning em log).

    Returns:
        Estatísticas: faces antes/depois, voxel_size usado.
    """
    import bpy

    log = logging.getLogger(__name__)
    faces_before = len(obj.data.polygons)
    if faces_before == 0 or distance <= 0:
        return {"faces_before": faces_before, "faces_after": faces_before, "voxel_size": 0.0}

    # Anti-OOM: descer grelha pela RAM disponível *antes* do solidify/OpenVDB.
    requested_grid = max(MORPH_MIN_GRID_AXIS, int(max_grid_axis))
    max_grid_axis = adapt_morph_max_grid_axis(
        available_ram_bytes(),
        requested=requested_grid,
    )
    if max_grid_axis < requested_grid:
        log.warning(
            "morphological_close: grelha %d→%d (RAM adapt; MemAvailable anti-OOM)",
            requested_grid,
            max_grid_axis,
        )

    # Input HI (edifício MC 1-4M faces): solidify x2 rebenta RAM. Remesh
    # resolve à escala do voxel — pré-decimar até tecto da grelha.
    face_cap = morph_input_face_cap(max_grid_axis)
    faces_pre = faces_before
    if faces_before > face_cap:
        from aigamekit_shared.mesh_repair_arrays import (
            extract_arrays,
            replace_mesh_arrays,
            simplify_faces_arrays,
        )

        co, tris = extract_arrays(obj)
        out = simplify_faces_arrays(co, tris, face_cap)
        if out is not None:
            replace_mesh_arrays(obj, out[0], out[1])
            faces_before = len(obj.data.polygons)
            log.warning(
                "morphological_close: pré-decimate %d→%d faces (cap=%d, anti-OOM solidify)",
                faces_pre,
                faces_before,
                face_cap,
            )
        else:
            mod = obj.modifiers.new("MorphPreDecimate", "DECIMATE")
            mod.decimate_type = "COLLAPSE"
            mod.ratio = max(face_cap / float(faces_before), 1e-4)
            bpy.context.view_layer.objects.active = obj
            bpy.ops.object.modifier_apply(modifier=mod.name)
            faces_before = len(obj.data.polygons)
            log.warning(
                "morphological_close: pré-decimate (bpy) %d→%d faces (cap=%d)",
                faces_pre,
                faces_before,
                face_cap,
            )

    # Rede de segurança (depois do pré-decimate, para que uma reversão devolva a
    # malha já dentro do orçamento de faces): o remesh VOXEL (OpenVDB) só assina
    # o campo a partir de uma superfície **fechada**. Com a malha aberta — o caso
    # normal a seguir a ``remove_internal_shells`` — devolve uma crosta fina:
    # medido no ``shepherd_cottage``, 75.8 → 0.8 m³ logo no primeiro remesh, e o
    # ``clean`` entregue ficava com o dobro da área e 9% do volume (sólido
    # transformado em casca dupla). Pior, o voxel efectivo sai de
    # :func:`adapt_morph_max_grid_axis`, que lê a RAM livre — o mesmo asset saía
    # sólido (grelha ≤449) ou oco (≥500) conforme a memória da máquina.
    vol_before = _signed_volume(obj)
    bnd_before = count_boundary_edges_fast(obj)
    morph_backup = obj.data.copy() if abs(vol_before) > _MORPH_MIN_VOLUME else None
    if bnd_before and morph_backup is not None:
        log.info(
            "morphological_close: entrada com %d arestas de fronteira — o remesh voxel pode "
            "degenerar (guard de volume activo)",
            bnd_before,
        )

    # Piso do remesh: nunca maior que distance/2 (senão distance≈0.005 vira grelha 1cm).
    vox_floor = min(0.01, max(distance / 2.0, 0.001))
    vox = voxel_size if voxel_size is not None else max(distance / 3.0, vox_floor)
    # Cap de grelha ~800 por eixo: assets em metros reais (edificio 7 m) com
    # distance milimetrico dariam voxel sub-mm (OOM/horas). O voxel nunca
    # desce abaixo de max_dim/max_grid_axis.
    max_dim = float(max(obj.dimensions)) if max(obj.dimensions) > 0 else 0.0
    if max_dim > 0:
        vox = max(vox, max_dim / float(max_grid_axis))
    # Grelha mais grossa que a distância pedida (auto morph em assets grandes
    # cai sempre aqui: 0.125xchar/octree ≪ char/800): com voxel > distance/2 o
    # dilate/erode é sub-voxel (não fecha vão nenhum) e a parede solidificada
    # (< 2 voxels) desaparece no remesh. Sobe o fecho para a escala do voxel e
    # garante parede ≥ ~2.2 voxels para sobreviver à grelha.
    dist_eff = float(distance)
    grid_clamped = vox > float(distance) / 2.0
    if grid_clamped:
        dist_eff = max(float(distance), vox)
        log.warning(
            "morphological_close: voxel %.4f m > distance/2 (grelha %d, asset %.2f m) — "
            "fecho efetivo %.4f m (vãos < %.4f m)",
            vox,
            max_grid_axis,
            max_dim,
            dist_eff,
            2.0 * dist_eff,
        )
    if wall_thickness is not None:
        wall = float(wall_thickness)
    elif grid_clamped:
        wall = max(2.2 * vox, 0.001)
    else:
        # wall = distance (nao 2x): solidify extra era o que derretia com morph leve.
        wall = max(dist_eff, 0.001)
    bpy.context.view_layer.objects.active = obj

    def _apply(mod_name: str) -> None:
        bpy.ops.object.modifier_apply(modifier=mod_name)

    def _voxel() -> None:
        nonlocal vox, max_grid_axis, wall, dist_eff, grid_clamped
        attempts = 0
        while True:
            mod = obj.modifiers.new("MorphVoxel", "REMESH")
            mod.mode = "VOXEL"
            mod.voxel_size = vox
            mod.use_smooth_shade = True
            try:
                _apply(mod.name)
                return
            except Exception as exc:
                # Remover modifier pendente se apply falhou a meio.
                if mod.name in obj.modifiers:
                    obj.modifiers.remove(mod)
                msg = str(exc).lower()
                memish = isinstance(exc, MemoryError) or "memory" in msg or "alloc" in msg
                if not memish or attempts >= 3 or max_grid_axis <= MORPH_MIN_GRID_AXIS:
                    raise
                attempts += 1
                new_grid = max(MORPH_MIN_GRID_AXIS, max_grid_axis // 2)
                log.warning(
                    "morphological_close: voxel remesh falhou (%s) — retry grelha %d→%d",
                    type(exc).__name__,
                    max_grid_axis,
                    new_grid,
                )
                max_grid_axis = new_grid
                if max_dim > 0:
                    vox = max(vox, max_dim / float(max_grid_axis))
                grid_clamped = vox > float(distance) / 2.0
                if grid_clamped:
                    dist_eff = max(float(distance), vox)
                    if wall_thickness is None:
                        wall = max(2.2 * vox, 0.001)

    def _set_group_weights(name: str, weights: np.ndarray) -> str:
        if name in obj.vertex_groups:
            obj.vertex_groups.remove(obj.vertex_groups[name])
        vg = obj.vertex_groups.new(name=name)
        for i, w in enumerate(weights):
            if w > 0.0:
                vg.add([i], float(w), "REPLACE")
        return str(vg.name)

    def _displace(amount: float, group: str | None = None) -> None:
        mod = obj.modifiers.new("MorphDisp", "DISPLACE")
        mod.mid_level = 0.0
        mod.strength = amount
        mod.direction = "NORMAL"
        if group:
            mod.vertex_group = group
        _apply(mod.name)

    def _decimate_back() -> None:
        """Decima o remesh inflado de volta a ``faces_before``.

        fast_simplification (quadric C++, ~10-15x) quando disponível; senão
        TRIANGULATE + DECIMATE modifiers (fallback lento).
        """
        faces_remeshed = tri_count(obj.data)
        if faces_remeshed <= faces_before or faces_before <= 0:
            return
        from aigamekit_shared.mesh_repair_arrays import (
            extract_arrays,
            replace_mesh_arrays,
            simplify_faces_arrays,
        )

        co, tris = extract_arrays(obj)
        out = simplify_faces_arrays(co, tris, faces_before)
        if out is not None:
            replace_mesh_arrays(obj, out[0], out[1])
            return
        mod = obj.modifiers.new("MorphTri", "TRIANGULATE")
        _apply(mod.name)
        mod = obj.modifiers.new("MorphDecimate", "DECIMATE")
        mod.decimate_type = "COLLAPSE"
        mod.ratio = faces_before / faces_remeshed
        _apply(mod.name)

    if wall > 0:
        mod = obj.modifiers.new("MorphSolidify", "SOLIDIFY")
        mod.thickness = wall
        mod.offset = -1.0  # engrossa para dentro; superfície exterior intacta
        mod.use_rim = True
        mod.use_quality_normals = True
        _apply(mod.name)

    if grid_clamped:
        # Fecho degradado à escala da grelha: com voxel > distance/2, o
        # dilate/erode é sub-voxel (no-op geométrico que paga 2 remeshes
        # extra). Uma única passagem de voxel remesh já funde shells/vãos
        # (~1 voxel) e sela as rachas — o efeito de fecho disponível nesta
        # escala — por ~1/3 do custo da cadeia completa.
        _voxel()
        faces_remeshed = tri_count(obj.data)
        _decimate_back()
        reverted = 0
        if morph_backup is not None:
            vol_after = _signed_volume(obj)
            if abs(vol_after) < _MORPH_MIN_VOLUME_RATIO * abs(vol_before):
                _restore_morph_backup(obj, morph_backup, vol_before, vol_after)
                reverted = 1
                morph_backup = None
        if morph_backup is not None:
            bpy.data.meshes.remove(morph_backup)
        faces_after = len(obj.data.polygons)
        log.info(
            "morphological_close (grid-clamped, remesh único): %d→%d faces (remesh=%d voxel=%.4f wall=%.4f)",
            faces_before,
            faces_after,
            faces_remeshed,
            vox,
            wall,
        )
        return {
            "faces_before": faces_before,
            "faces_after": faces_after,
            "voxel_size": vox,
            "wall_thickness": wall,
            "distance_eff": dist_eff,
            "reverted_volume_collapse": reverted,
        }

    _voxel()  # normaliza normais antes do displace

    if gap_mask:
        # Dilatação ponderada pelo vão frontal: só rachas fecham a 100%.
        open_d = gap_open_dist if gap_open_dist is not None else 2.5 * dist_eff
        ao_w = _vertex_gap_weights(obj, full_dist=dist_eff, zero_dist=open_d)
        group = _set_group_weights("MorphCloseAO", ao_w)
        _displace(dist_eff, group)
        # Posições pós-dilatação (antes do voxel destruir os vgroups) para
        # transferir os pesos por vizinho-mais-próximo à mesh fundida.
        me = obj.data
        n_pre = len(me.vertices)
        pre_coords_flat = np.empty(n_pre * 3, dtype=np.float64)
        me.vertices.foreach_get("co", pre_coords_flat)
        pre_coords: np.ndarray = pre_coords_flat.reshape(-1, 3)
        _voxel()  # dilatação funde as margens da racha

        from mathutils import Vector as _Vec
        from mathutils.kdtree import KDTree

        kd = KDTree(n_pre)
        for i, co in enumerate(pre_coords):
            kd.insert(_Vec(co), i)
        kd.balance()
        me = obj.data
        post_w = np.zeros(len(me.vertices), dtype=np.float64)
        for i, v in enumerate(me.vertices):
            _co, idx, _dist = kd.find(v.co)
            if idx is not None:
                post_w[i] = ao_w[idx]
        group = _set_group_weights("MorphCloseAO", post_w)
        _displace(-dist_eff, group)
        if "MorphCloseAO" in obj.vertex_groups:
            obj.vertex_groups.remove(obj.vertex_groups["MorphCloseAO"])
    else:
        _displace(dist_eff)
        _voxel()  # dilatação funde as margens da racha
        _displace(-dist_eff)

    _voxel()  # erosão limpa auto-interseções e devolve a superfície

    # Voxel remesh infla faces (grelha densa >> tri budget do MC) e produz
    # quads (2 tris cada no export). Triangular e decimar de volta ao tri
    # count original: mantém o fecho sem herdar a densidade da grelha
    # (capela: 1.7M → 7.5M tris sem isto).
    faces_remeshed = tri_count(obj.data)
    _decimate_back()

    reverted = 0
    if morph_backup is not None:
        vol_after = _signed_volume(obj)
        if abs(vol_after) < _MORPH_MIN_VOLUME_RATIO * abs(vol_before):
            _restore_morph_backup(obj, morph_backup, vol_before, vol_after)
            reverted = 1
            morph_backup = None
    if morph_backup is not None:
        bpy.data.meshes.remove(morph_backup)

    faces_after = len(obj.data.polygons)
    log.info(
        "morphological_close: %d→%d faces (remesh=%d dist=%.4f voxel=%.4f wall=%.4f gap_mask=%s)",
        faces_before,
        faces_after,
        faces_remeshed,
        dist_eff,
        vox,
        wall,
        gap_mask,
    )
    return {
        "faces_before": faces_before,
        "faces_after": faces_after,
        "voxel_size": vox,
        "wall_thickness": wall,
        "distance_eff": dist_eff,
        "reverted_volume_collapse": reverted,
    }


def infer_up_axis(coords: np.ndarray) -> int:
    """Eixo vertical em coords locais = maior extensão AABB (clamp flare / local)."""
    if coords.size == 0:
        return 1
    ext = coords.max(axis=0) - coords.min(axis=0)
    return int(np.argmax(ext))


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

    from aigamekit_shared.bpy_mesh import _require_bpy

    _require_bpy()
    if obj is None or getattr(obj, "type", None) != "MESH":
        return 0
    if len(obj.data.vertices) == 0:
        return 0

    mesh = obj.data
    coords = vertex_coords(mesh)
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

    from aigamekit_shared.bpy_mesh import _require_bpy

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
        deltas: list[tuple[float, float, float] | None] = [None] * len(bm.verts)
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
    """Erode abas penduradas em arestas de fronteira (abas internas do MC).

    Só são apagadas faces com **≥2** arestas de fronteira. Num triângulo o
    balanço é exacto: apagar uma face com 3 arestas de fronteira tira 3 da
    contagem, com 2 arestas tira 2 e promove 1 (net -1), mas com **1** aresta
    tira 1 e promove 2 (net **+1**) — ou seja, erodir o rebordo de um buraco
    normal alarga-o em vez de o fechar. A versão anterior apagava tudo o que
    tocasse a fronteira e por isso subia a contagem: medido no
    ``city_wall_seg_c``, 314 → 502 arestas de fronteira com 1240 faces
    apagadas, dentro do orçamento de 1% e sem qualquer aviso.

    Guards: orçamento de ``max_faces_ratio`` das faces e reversão se a
    fronteira não tiver diminuído no fim.

    Returns:
        Faces removidas (0 se o passo foi revertido).
    """
    import bmesh

    before = count_boundary_edges_fast(obj)
    if before == 0:
        return 0
    backup = obj.data.copy()

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    total = len(bm.faces)
    budget = max(1, int(max_faces_ratio * total))
    removed = 0
    for _ in range(max_iters):
        bm.edges.ensure_lookup_table()
        doomed = [f for f in bm.faces if sum(1 for e in f.edges if len(e.link_faces) == 1) >= 2]
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

    if removed and count_boundary_edges_fast(obj) >= before:
        log.warning(
            "_erode_boundary_flaps: fronteira não desceu (%d) com %d faces apagadas — revertido",
            before,
            removed,
        )
        import bpy

        old = obj.data
        obj.data = backup
        bpy.data.meshes.remove(old)
        return 0

    import bpy

    bpy.data.meshes.remove(backup)
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
            res = bmesh.ops.holes_fill(bm, edges=edges, sides=0)
            if res.get("faces"):
                capped += 1
                continue
        except Exception:
            pass
        try:
            res = bmesh.ops.contextual_create(bm, geom=edges)
            if res.get("faces"):
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


# Fecho transitivo (CC em arrays / remove_doubles) colapsa a malha se o limiar
# de weld ≥ aresta mediana — cada aresta vira par de fusão e o mesh vira 1 ponto.
_WELD_MEDIAN_EDGE_CAP = 0.4


def dynamic_weld_distance(vertex_count: int, median_edge: float | None = None) -> float:
    """Distância de weld adaptativa pela densidade de vértices (topology-fix).

    Malhas densas (>150k) usam limiar menor para preservar detalhe; malhas
    leves (<50k) usam limiar maior para fechar rachaduras de marching cubes.

    Se ``median_edge`` for dado, o limiar é capped a ``0.4 x mediana`` para o
    fecho transitivo não fundir a malha inteira (lily_pad/slime: dyn=0.008 com
    mediana≈0.008 → 0 faces no engine arrays).
    """
    if vertex_count > 150_000:
        dist = 0.003
    elif vertex_count > 100_000:
        dist = 0.005
    elif vertex_count > 50_000:
        dist = 0.008
    else:
        dist = 0.01
    if median_edge is not None and median_edge > 0:
        dist = min(dist, _WELD_MEDIAN_EDGE_CAP * float(median_edge))
    return dist


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
    # Agulhas por edge collapse (não abre buracos) em vez de delete.
    sliver_collapse: bool = True
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
    post_decimate_sliver_collapse: bool = True


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
        # Weld + debris/slivers + fill + watertight seletivo.
        # Sem remove_internal_shells / flare / Taubin (destruíam edifícios
        # casca-plástico). Flap-erode ligado: fecha rachas MC non-manifold
        # (capela 638→0 boundary sem matar faces). Diâmetro de loop limita
        # portas/janelas grandes.
        do_remove_internal_shells=False,
        fill_holes_sides=96,
        watertight=True,
        watertight_cap_base=True,
        watertight_final_fill=True,
        watertight_skip_flap_erode=False,
        watertight_max_loop_diameter_ratio=0.35,
        watertight_max_loop_edges=400,
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
    sliver_collapse: bool = True,
) -> dict[str, int]:
    """Limpeza leve após Decimate COLLAPSE (preserva UVs).

    dissolve → weld exacto → slivers → triangulate. Sem watertight/debris
    (o Decimate já alterou a topologia; o objectivo é só remover agulhas).
    Agulhas são colapsadas, não apagadas (``sliver_collapse``) — apagar num
    LOD fechado abre buracos que nada a jusante volta a fechar.
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
            stats["sliver_faces"] = remove_sliver_faces(obj, max_aspect=sliver_max_aspect, collapse=sliver_collapse)
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
            sliver_collapse=prof.post_decimate_sliver_collapse,
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
        sliver_collapse=prof.sliver_collapse,
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
    sliver_collapse: bool = True,
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
    (opt) → clamp flare → Taubin → normais.

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
        med_edge: float | None = None
        try:
            me = obj.data
            me.calc_loop_triangles()
            n_tri = len(me.loop_triangles)
            if n_tri > 0:
                co_flat = np.empty(len(me.vertices) * 3, dtype=np.float64)
                me.vertices.foreach_get("co", co_flat)
                co = co_flat.reshape(-1, 3)
                tri_flat = np.empty(n_tri * 3, dtype=np.int32)
                me.loop_triangles.foreach_get("vertices", tri_flat)
                tri = tri_flat.reshape(-1, 3)
                # Amostra até 64k arestas (3 por tri) — basta para a mediana.
                e0 = tri[:, 0]
                e1 = tri[:, 1]
                sample = min(len(tri), 64_000)
                if sample < len(tri):
                    rng = np.random.default_rng(0)
                    idx = rng.choice(len(tri), size=sample, replace=False)
                    e0, e1 = e0[idx], e1[idx]
                med_edge = float(np.median(np.linalg.norm(co[e1] - co[e0], axis=1)))
        except Exception as exc:
            log.debug("median_edge para weld_density falhou: %s", exc)
        dist = dynamic_weld_distance(len(obj.data.vertices), median_edge=med_edge)
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
            stats["sliver_faces"] = remove_sliver_faces(obj, max_aspect=sliver_max_aspect, collapse=sliver_collapse)
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
            stats["boundary_after"] = count_boundary_edges_fast(obj)
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
        # Smooth vertex normals (não omitir NORMAL — viewers ficavam flat), mas
        # sem arestas duras: a 60° o exporter parte loops nas creases e o fecho
        # watertight reabre no reimport (boundary > 0). 180° = tudo smooth.
        from aigamekit_shared.bpy_mesh import smooth_shade_scene

        smooth_shade_scene(meshes, degrees=180.0)
        export_kw["export_normals"] = True
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

    from aigamekit_shared.mesh_repair_arrays import extract_arrays

    verts, faces = extract_arrays(obj)
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
    obj = create_mesh_from_arrays(verts, faces, name="aigamekit_fix")
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
    obj = create_mesh_from_arrays(verts, faces, name="aigamekit_cap")
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
