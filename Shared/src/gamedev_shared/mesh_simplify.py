"""Simplificação unificada de malha (Decimate COLLAPSE + perfis mesh_repair).

Usado por Text3D LOD / ``remesh-textured`` / ``simplify`` e pelo stage
``_to_paint`` do GameAssets. Não usar voxel remesh aqui — isso destrói
paredes finas e janelas (edifícios casca-plástico).

Pipeline canónico:

1. (opcional) merge by distance — **off por defeito**: em meshes
   texturados o weld pré-COLLAPSE trava o Decimate cedo (~20k faces;
   lod1/lod2 idênticos). O ``post_decimate`` já solda degenerados.
2. perfil ``pre_decimate_uv`` (se ``repair``)
3. Decimate COLLAPSE em passes ≤50% (rácios extremos são no-op no bpy),
   com passe final exato para cravar o alvo e weld de recuperação em
   estagnação (verts duplicados V/Tri≈3 bloqueiam o COLLAPSE).
4. perfil ``post_decimate`` (se ``repair``)
5. shade smooth by angle

Garantias de consistência:

* **Rig/skin**: o Decimate interpola vertex groups nativamente; o modifier
  é movido para o topo da stack antes do apply para nunca congelar o
  Armature modifier (senão skins[] desaparece / anims morrem no export).
* **Shape keys**: ``modifier_apply`` é impossível com shape keys — o
  objecto é saltado com warning em vez de rebentar a pipeline.
* **UVs/materiais**: preservados pelo COLLAPSE; texturas exportadas com
  ``export_image_format="AUTO"`` (sem recompressão JPEG destrutiva).
* **Normais/tangentes**: shade smooth by angle (normais por vértice, sem
  V/Tri≈3); tangentes MikkTSpace ficam a cargo do exporter (``save_glb``
  liga-as automaticamente quando há normal map).
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

# Weld de duplicados exactos (GLBs V/Tri≈3). Igual ao usado em
# smooth_shade_scene / mesh_lod.
_STAGNATION_WELD_DIST = 1e-4

# Deteção de triangle soup: mesh tri conexo tem V ≈ F/2; soup (verts
# partidos por canto, V/Tri≈3) tem V = 3F. Acima deste rácio, weld exacto
# é obrigatório antes do COLLAPSE — decimar soup apaga triângulos isolados
# em silêncio ("moth-eaten") em vez de colapsar arestas.
_SOUP_VERTS_PER_FACE = 1.5


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


def has_shape_keys(obj: Any) -> bool:
    """True se o mesh object tem shape keys (morph targets glTF)."""
    keys = getattr(getattr(obj, "data", None), "shape_keys", None)
    return bool(keys and getattr(keys, "key_blocks", None))


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
    return str(vg.name)


def _weld_if_split_soup(obj: Any) -> int:
    """Weld exacto (1e-4) quando o mesh é triangle soup (V/Tri≈3).

    GLBs re-exportados com normais por canto chegam com todos os vértices
    partidos: cada aresta é "fronteira" e o Decimate COLLAPSE, sem arestas
    partilhadas, degenera triângulos isolados um a um — o LOD sai roto
    ("moth-eaten") sem nunca estagnar. O weld só dispara no caso soup;
    meshes texturados conexos (seams UV) não são tocados — weld cego
    pré-COLLAPSE trava o rácio (~20k piso medido no crate).

    Returns:
        Número de vértices fundidos (0 se não era soup).
    """
    n_faces = len(obj.data.polygons) or 1
    n_verts = len(obj.data.vertices)
    if n_verts <= _SOUP_VERTS_PER_FACE * n_faces:
        return 0
    from gamedev_shared.mesh_repair import remove_doubles

    welded = int(remove_doubles(obj, threshold=_STAGNATION_WELD_DIST) or 0)
    if welded:
        log.info(
            "Triangle soup detectado (%d verts / %d faces) — weld exacto fundiu %d verts antes do COLLAPSE",
            n_verts,
            n_faces,
            welded,
        )
    return welded


def _triangulate_if_needed(bpy: Any, obj: Any) -> bool:
    """Triangula o mesh se tiver quads/n-gons (COLLAPSE ignora-os).

    Voxel remesh devolve 100% quads; o Decimate COLLAPSE não reduz quads
    (medido: 13.5k quads → alvo 3k → no-op total). O exporter glTF
    triangula sempre, portanto isto não muda o entregável.

    Returns:
        True se foi triangulado.
    """
    mesh = obj.data
    if len(mesh.loops) == 3 * len(mesh.polygons):
        return False
    mod = obj.modifiers.new("Triangulate", "TRIANGULATE")
    if len(obj.modifiers) > 1:
        with contextlib.suppress(Exception):
            bpy.ops.object.modifier_move_to_index(modifier=mod.name, index=0)
    bpy.ops.object.modifier_apply(modifier=mod.name)
    log.info("Triangulado antes do Decimate: %d polygons → %d tris", len(mesh.polygons), len(obj.data.polygons))
    return True


def _apply_decimate_modifier(bpy: Any, obj: Any, ratio: float, protect_group: str | None) -> None:
    """Cria, move para o topo da stack e aplica um Decimate COLLAPSE.

    Mover para índice 0 garante que o apply nunca congela modifiers de
    deformação anteriores (Armature) — o Decimate corre sobre o mesh base
    em rest-pose e o rig/skin/animações sobrevivem intactos ao export.
    """
    mod = obj.modifiers.new("Decimate", "DECIMATE")
    mod.decimate_type = "COLLAPSE"
    mod.ratio = ratio
    mod.use_collapse_triangulate = True
    if protect_group:
        mod.vertex_group = protect_group
        mod.invert_vertex_group = True
        mod.vertex_group_factor = 1.0
    if len(obj.modifiers) > 1:
        with contextlib.suppress(Exception):
            bpy.ops.object.modifier_move_to_index(modifier=mod.name, index=0)
    bpy.ops.object.modifier_apply(modifier=mod.name)


def decimate_mesh_object(
    obj: Any,
    target_faces: int,
    *,
    protect_boundaries: bool = True,
    max_passes: int = 12,
    slack: float = 1.2,
    min_ratio_per_pass: float = 0.5,
) -> int:
    """Aplica Decimate COLLAPSE até ``target_faces`` (com clamp).

    Blender COLLAPSE **ignora** rácios extremos numa só passagem (ex.: crate
    159k→alvo 1.5k com ratio=0.009 → ~22k e para). Estratégia: cortar no
    máximo ``min_ratio_per_pass`` (default 50%) por passe e repetir até
    ``faces <= target`` ou estagnação topológica. O passe final usa o rácio
    exato para cravar o alvo (``slack`` é só a banda de tolerância aceite
    quando a topologia estagna perto do alvo).

    O alvo é clampado **uma vez** contra a contagem inicial de faces — o
    piso heurístico (:data:`_MIN_DECIMATE_FRAC`) não erode passe a passe.

    Recuperações de estagnação, por ordem: (1) desligar protecção de
    fronteira; (2) weld exacto ``1e-4`` — GLBs com verts já duplicados
    (V/Tri≈3) bloqueiam o COLLAPSE e rasgam o LOD ("moth-eaten").

    Meshes com shape keys não são decimáveis (``modifier_apply`` é
    impossível) — devolve a contagem atual com warning.

    Returns:
        Número de faces após a decimação.
    """
    import bpy

    n_now = len(obj.data.polygons)
    if has_shape_keys(obj):
        log.warning(
            "Decimate saltado em %r: mesh tem shape keys (modifier_apply impossível); %d faces mantidas",
            getattr(obj, "name", "?"),
            n_now,
        )
        return n_now
    if n_now <= clamp_decimate_target(n_now, target_faces):
        return n_now

    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    # Quads/n-gons (voxel remesh) são invisíveis ao COLLAPSE — triangular.
    _triangulate_if_needed(bpy, obj)
    n_now = len(obj.data.polygons)
    target = clamp_decimate_target(n_now, target_faces)
    if n_now <= target:
        return n_now
    # Soup (V/Tri≈3): reconectar ANTES do loop — decimar soup apaga
    # triângulos em vez de colapsar arestas (destrutivo e silencioso).
    weld_tried = _weld_if_split_soup(obj) > 0
    use_protect = protect_boundaries

    for attempt in range(max(1, int(max_passes))):
        n_now = len(obj.data.polygons)
        if n_now <= target:
            break

        desired = max(1e-6, min(1.0, target / n_now))
        # Cap de agressividade: ratio baixo demais é no-op no bpy.
        ratio = max(desired, float(min_ratio_per_pass))
        protect_group = protect_boundary_vertices(obj) if use_protect else None
        _apply_decimate_modifier(bpy, obj, ratio, protect_group)
        if protect_group and protect_group in obj.vertex_groups:
            obj.vertex_groups.remove(obj.vertex_groups[protect_group])

        n_after = len(obj.data.polygons)
        log.info(
            "Após decimate pass %d: %d→%d faces (ratio=%.4f, alvo=%d)",
            attempt + 1,
            n_now,
            n_after,
            ratio,
            target,
        )
        if n_after >= int(n_now * 0.98):
            if use_protect:
                use_protect = False
                log.info("Decimate estagnou com protect — retry sem protecção de fronteira")
                continue
            if not weld_tried:
                weld_tried = True
                from gamedev_shared.mesh_repair import remove_doubles

                welded = remove_doubles(obj, threshold=_STAGNATION_WELD_DIST)
                if welded:
                    log.info(
                        "Decimate estagnou — weld de recuperação fundiu %d verts duplicados (V/Tri≈3); retry",
                        welded,
                    )
                    continue
            if n_after <= int(target * slack):
                log.info("Decimate estagnou em %d faces — dentro da banda slack do alvo %d", n_after, target)
            else:
                log.warning(
                    "Decimate estagnou em %d faces (alvo %d) — piso topológico",
                    n_after,
                    target,
                )
            break

    return len(obj.data.polygons)


def simplify_mesh_object(
    obj: Any,
    target_faces: int,
    *,
    repair: bool = True,
    pre_merge_threshold: float = 0.0,
    smooth_degrees: float = 60.0,
    protect_boundaries: bool = False,
) -> dict[str, int]:
    """Pipeline unificado de simplificação sobre um bpy mesh object.

    Args:
        obj: Objecto MESH do Blender.
        target_faces: Orçamento de faces desejado.
        repair: Se True, aplica ``pre_decimate_uv`` / ``post_decimate``.
        pre_merge_threshold: Merge by distance antes do reparo. Default 0
            (off) — weld pré-COLLAPSE trava LOD texturado (~20k piso).
        smooth_degrees: Ângulo para ``apply_smooth_by_angle`` (0 desliga).
        protect_boundaries: Protege rebordos abertos no Decimate. Default
            False — em meshes texturados (UV seams) o protect trava o
            COLLAPSE cedo (~20k faces) e lod1/lod2 ficam idênticos.

    Returns:
        Estatísticas (faces_before, faces_after, verts_before, verts_after).
    """
    from gamedev_shared.bpy_mesh import apply_smooth_by_angle
    from gamedev_shared.mesh_repair import remove_doubles, repair_mesh_object_with_profile

    stats: dict[str, int] = {
        "faces_before": len(obj.data.polygons),
        "verts_before": len(obj.data.vertices),
    }

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

    decimate_mesh_object(obj, target_faces, protect_boundaries=protect_boundaries)

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
    stats["verts_after"] = len(obj.data.vertices)
    return stats


def _split_face_budget(mesh_objs: list[Any], target_faces: int) -> list[int]:
    """Distribui o orçamento de faces proporcionalmente por objecto.

    Cada objecto recebe pelo menos 4 faces; o resto do orçamento é
    proporcional à sua quota da contagem total (LODs multi-mesh mantêm o
    balanço relativo de detalhe entre partes).
    """
    counts = [len(o.data.polygons) for o in mesh_objs]
    total = sum(counts) or 1
    return [max(4, round(target_faces * c / total)) for c in counts]


def simplify_glb(
    path_in: str | Path,
    path_out: str | Path,
    *,
    target_faces: int,
    repair: bool = True,
) -> Path:
    """Simplifica GLB (geometria) via Decimate COLLAPSE + perfis Shared.

    Todas as meshes do ficheiro são simplificadas (orçamento proporcional
    à contagem de faces de cada uma) e **todas** são re-exportadas —
    assets multi-parte não perdem meshes secundárias. Armatures, skins,
    animações e shape keys sobrevivem (meshes com shape keys não são
    decimadas — ver :func:`decimate_mesh_object`).

    Texturas/UVs/materiais são preservados sem recompressão
    (``export_image_format="AUTO"``); não faz downscale de textura
    (isso fica em ``remesh_textured_glb``). Tangentes MikkTSpace são
    escritas automaticamente quando há normal map (``save_glb``).

    Args:
        path_in: GLB de entrada.
        path_out: GLB de saída.
        target_faces: Orçamento de faces (total, todas as meshes).
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
    n_total = sum(len(o.data.polygons) for o in mesh_objs)
    if n_total < 4:
        raise ValueError(f"Mesh com poucas faces ({n_total}); simplify não aplicável.")
    log.info("simplify_glb: %d faces em %d mesh(es) → ~%d", n_total, len(mesh_objs), target_faces)

    budgets = _split_face_budget(mesh_objs, target_faces)
    faces_after = 0
    for obj, budget in zip(mesh_objs, budgets, strict=True):
        if len(mesh_objs) > 1:
            log.info(
                "simplify_glb: %r %d faces → ~%d (quota)",
                getattr(obj, "name", "?"),
                len(obj.data.polygons),
                budget,
            )
        stats = simplify_mesh_object(obj, budget, repair=repair)
        faces_after += stats["faces_after"]

    # Include armatures so skins + animations survive LOD simplify.
    import bpy

    arms = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
    export_objs: list[Any] = [*mesh_objs, *arms]
    save_glb(export_objs, path_out, export_apply=not arms, export_image_format="AUTO")
    clear_scene()
    log.info("Resultado: %s (%d faces)", path_out, faces_after)
    return path_out
