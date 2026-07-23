"""Simplificação unificada de malha (Decimate COLLAPSE + perfis mesh_repair).

Usado por Text3D LOD / ``remesh-textured`` / ``simplify`` e pelo stage
``_to_paint`` do GameAssets. Não usar voxel remesh aqui — isso destrói
paredes finas e janelas (edifícios casca-plástico).

Pipeline canónico:

1. merge by distance (micro-cracks)
2. perfil ``pre_decimate_uv``
3. Decimate COLLAPSE (piso heurístico + protecção de fronteira)
4. perfil ``post_decimate``
5. shade smooth by angle
"""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_LOD_PROTECT_GROUP = "_lod_boundary_protect"

# Piso de decimação: abaixo disto, features finas colapsam em agulhas.
_MIN_DECIMATE_FRAC = 0.008
_MIN_DECIMATE_FACES = 150


def clamp_decimate_target(n_faces: int, requested: int) -> int:
    """Sobe o target se o rácio pedido for demasiado agressivo.

    Heurística: ``max(requested, MIN_FACES, ⌊n · MIN_FRAC⌋)``.
    """
    req = max(4, int(requested))
    if n_faces <= req:
        return req
    floor = max(_MIN_DECIMATE_FACES, int(n_faces * _MIN_DECIMATE_FRAC))
    if req < floor:
        log.warning(
            "Target %d abaixo do piso heurístico %d (%.2f%% de %d faces) — a subir",
            req,
            floor,
            100.0 * _MIN_DECIMATE_FRAC,
            n_faces,
        )
        return floor
    return req


def protect_boundary_vertices(obj: Any) -> str | None:
    """Vertex group com vértices de fronteira (rebordos abertos).

    Se >5% dos verts forem "fronteira", assume-se poluição de seams UV e a
    protecção é ignorada.

    Returns:
        Nome do grupo criado, ou None se a mesh não tem fronteiras úteis.
    """
    import bmesh

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    n_verts = len(bm.verts) or 1
    boundary_verts = {v.index for e in bm.edges if len(e.link_faces) == 1 for v in e.verts}
    bm.free()
    if not boundary_verts:
        return None
    frac = len(boundary_verts) / n_verts
    if frac > 0.05:
        log.info(
            "Proteção de fronteira ignorada: %d/%d verts (%.1f%%) — seams UV, não buracos",
            len(boundary_verts),
            n_verts,
            100 * frac,
        )
        return None

    vg = obj.vertex_groups.get(_LOD_PROTECT_GROUP) or obj.vertex_groups.new(name=_LOD_PROTECT_GROUP)
    vg.add(list(boundary_verts), 1.0, "REPLACE")
    log.info("Proteção de fronteira: %d vértices em rebordos abertos", len(boundary_verts))
    return vg.name


def decimate_mesh_object(
    obj: Any,
    target_faces: int,
    *,
    protect_boundaries: bool = True,
) -> int:
    """Aplica Decimate COLLAPSE até ``target_faces`` (com clamp).

    Returns:
        Número de faces após a decimação.
    """
    import bpy

    n_now = len(obj.data.polygons)
    target = clamp_decimate_target(n_now, target_faces)
    if n_now <= target:
        return n_now

    ratio = target / n_now
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    protect_group = protect_boundary_vertices(obj) if protect_boundaries else None
    mod = obj.modifiers.new("Decimate", "DECIMATE")
    mod.decimate_type = "COLLAPSE"
    mod.ratio = ratio
    mod.use_collapse_triangulate = True
    if protect_group:
        mod.vertex_group = protect_group
        mod.invert_vertex_group = True
        mod.vertex_group_factor = 1.0
    bpy.ops.object.modifier_apply(modifier=mod.name)
    if protect_group and protect_group in obj.vertex_groups:
        obj.vertex_groups.remove(obj.vertex_groups[protect_group])

    n_after = len(obj.data.polygons)
    log.info("Após decimate: %d faces (ratio=%.4f, alvo=%d)", n_after, ratio, target)
    return n_after


def simplify_mesh_object(
    obj: Any,
    target_faces: int,
    *,
    repair: bool = True,
    pre_merge_threshold: float = 0.0001,
    smooth_degrees: float = 60.0,
) -> dict[str, int]:
    """Pipeline unificado de simplificação sobre um bpy mesh object.

    Args:
        obj: Objecto MESH do Blender.
        target_faces: Orçamento de faces desejado.
        repair: Se True, aplica ``pre_decimate_uv`` / ``post_decimate``.
        pre_merge_threshold: Merge by distance antes do reparo (0 desliga).
        smooth_degrees: Ângulo para ``apply_smooth_by_angle`` (0 desliga).

    Returns:
        Estatísticas (faces_before, faces_after, …).
    """
    from gamedev_shared.bpy_mesh import apply_smooth_by_angle
    from gamedev_shared.mesh_repair import remove_doubles, repair_mesh_object_with_profile

    stats: dict[str, int] = {"faces_before": len(obj.data.polygons)}

    if pre_merge_threshold > 0:
        remove_doubles(obj, threshold=pre_merge_threshold)
        log.info("Após merge by distance: %d faces", len(obj.data.polygons))

    if repair:
        pre = repair_mesh_object_with_profile(obj, "pre_decimate_uv")
        if pre.get("long_edge_faces") or pre.get("sliver_faces") or pre.get("debris_faces"):
            log.info(
                "pre_decimate_repair: long_edges=%d slivers=%d debris=%d welded=%d",
                pre.get("long_edge_faces", 0),
                pre.get("sliver_faces", 0),
                pre.get("debris_faces", 0),
                pre.get("welded_relative", 0),
            )
        log.info("Após reparo pré-decimação: %d faces", len(obj.data.polygons))

    decimate_mesh_object(obj, target_faces, protect_boundaries=repair)

    if repair:
        post = repair_mesh_object_with_profile(obj, "post_decimate")
        if post.get("sliver_faces"):
            log.warning("Slivers pós-decimate: %d faces removidas", post["sliver_faces"])
        log.info("Após limpeza pós-decimação: %d faces", len(obj.data.polygons))

    if smooth_degrees > 0:
        mesh = obj.data
        with contextlib.suppress(Exception):
            mesh.free_normals_split()
        apply_smooth_by_angle(obj, smooth_degrees)

    stats["faces_after"] = len(obj.data.polygons)
    return stats


def simplify_glb(
    path_in: str | Path,
    path_out: str | Path,
    *,
    target_faces: int,
    repair: bool = True,
) -> Path:
    """Simplifica GLB (geometria) via Decimate COLLAPSE + perfis Shared.

    Preserva materiais/UVs se existirem; não faz downscale de textura
    (isso fica em ``remesh_textured_glb``).

    Args:
        path_in: GLB de entrada.
        path_out: GLB de saída.
        target_faces: Orçamento de faces.
        repair: Reparo pré/pós-decimate (default True).

    Returns:
        Path do ficheiro escrito.
    """
    from gamedev_shared.bpy_mesh import clear_scene, load_glb, save_glb

    path_in = Path(path_in)
    path_out = Path(path_out)
    path_out.parent.mkdir(parents=True, exist_ok=True)

    if path_out.exists() and path_out.stat().st_nlink > 1:
        path_out.unlink()
        log.info("Hardlink quebrado em %s (nlink>1)", path_out.name)

    objs = load_glb(path_in)
    mesh_objs = [o for o in objs if getattr(o, "type", None) == "MESH"]
    if not mesh_objs:
        raise ValueError(f"Mesh vazia: {path_in}")
    obj = max(mesh_objs, key=lambda o: len(o.data.polygons))
    n = len(obj.data.polygons)
    if n < 4:
        raise ValueError(f"Mesh com poucas faces ({n}); simplify não aplicável.")
    log.info("simplify_glb: %d faces → ~%d", n, target_faces)

    stats = simplify_mesh_object(obj, target_faces, repair=repair)

    # Include armatures so skins + animations survive LOD simplify.
    import bpy

    arms = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
    export_objs: list[Any] = [obj, *arms] if arms else [obj]
    save_glb(export_objs, path_out, export_apply=not arms)
    clear_scene()
    log.info("Resultado: %s (%d faces)", path_out, stats["faces_after"])
    return path_out
