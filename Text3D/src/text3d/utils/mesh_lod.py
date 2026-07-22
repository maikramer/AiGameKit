"""Geração de variantes LOD (níveis de detalhe) de meshes GLB via bpy.

Cada função opera sobre ficheiros GLB e preserva armatures, skin weights e
animações quando presentes no input.
"""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path

from gamedev_shared.bpy_mesh import clear_scene

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# bpy helpers (lazy import inside functions — bpy may not be installed)
# ---------------------------------------------------------------------------


def _require_bpy():
    """Importa bpy ou levanta ImportError com mensagem útil."""
    try:
        import bpy

        return bpy
    except ImportError:
        raise ImportError("bpy is required for LOD generation. Install with: pip install bpy") from None


def _shade_smooth(mesh_obj) -> None:
    """Marca faces como smooth-shaded, mantendo arestas duras só acima de 60°.

    Substitui o antigo ``_restore_smooth_normals`` que aplicava
    ``normals_split_custom_set(loop_normals)``. Esse caminho fazia o exporter
    GLTF do Blender escrever normais por canto de face, duplicando vértices
    (V/Tri ≈ 3 — bug do goblin_shape).

    Usa ``gamedev_shared.bpy_mesh.apply_smooth_by_angle`` (60°), que funciona no
    bpy 5.x via ``shade_smooth_by_angle`` — o antigo ``use_auto_smooth`` foi
    removido no Blender 4.1 e era silenciosamente ignorado, deixando a malha
    totalmente lisa.
    """
    from gamedev_shared.bpy_mesh import apply_smooth_by_angle

    mesh = mesh_obj.data
    with contextlib.suppress(Exception):
        # Limpa qualquer custom split normal data que possa ter ficado da geração
        mesh.free_normals_split()
    apply_smooth_by_angle(mesh_obj, 60.0)


def _dynamic_weld_distance(vertex_count: int) -> float:
    """Compat: delega em ``gamedev_shared.mesh_repair.dynamic_weld_distance``."""
    from gamedev_shared.mesh_repair import dynamic_weld_distance

    return dynamic_weld_distance(vertex_count)


def _load_glb_with_armatures(path: Path) -> tuple:
    """Importa GLB via bpy, devolve (mesh_obj, armature_objs).

    Limpa a cena antes de importar. Preserva transforms, armatures,
    shape keys e materiais. Meshopt-aware: em bpy < 5.2 descomprime antes
    via ``gltf-transform`` (``bpy_readable_glb``) — necessário para gerar a
    ladder a partir de um lod0 já finalizado com EXT_meshopt_compression.
    """
    import bpy

    from gamedev_shared.skin_transfer import bpy_readable_glb

    path = Path(path).expanduser().resolve()
    clear_scene()
    with bpy_readable_glb(path) as import_path:
        bpy.ops.import_scene.gltf(filepath=str(import_path))
    mesh_objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not mesh_objs:
        raise ValueError(f"No mesh objects found in {path}")
    # Usa a mesh com mais polígonos (mesh principal)
    mesh_obj = max(mesh_objs, key=lambda o: len(o.data.polygons))
    arm_objs = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
    return mesh_obj, arm_objs


def _export_textured_glb(
    output_path: Path,
    mesh_obj,
    arm_objs: list,
    *,
    export_normals: bool = True,
    export_tangents: bool | None = None,
) -> None:
    """Exporta mesh + texturas para GLB (geometry + materials + images).

    ``export_normals=False`` (usado após topology-fix watertight) evita que o
    exporter glTF parta vértices em arestas duras e reabra o volume no reimport.
    """
    import bpy

    export_objects = [mesh_obj, *arm_objs]
    bpy.ops.object.select_all(action="DESELECT")
    for o in export_objects:
        o.select_set(True)
    bpy.context.view_layer.objects.active = arm_objs[0] if arm_objs else mesh_obj
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    has_armature = bool(arm_objs)
    if export_tangents is None:
        export_tangents = bool(export_normals)
    bpy.ops.export_scene.gltf(
        filepath=str(output_path),
        export_format="GLB",
        use_selection=True,
        # apply=True freezes Armature modifier → skins[] omitted / anims die.
        export_apply=not has_armature,
        export_animations=has_armature,
        export_skins=has_armature,
        export_all_influences=False,
        export_normals=export_normals,
        # Tangents so the normal map has no seams at UV islands — decimated
        # LODs are especially prone to this without them.
        export_tangents=export_tangents,
        export_texcoords=True,
        export_materials="EXPORT",
        export_image_format="AUTO",
    )


_export_glb = _export_textured_glb


# Primitivas de reparação unificadas em gamedev_shared.mesh_repair.
from gamedev_shared.mesh_repair import fill_holes as _fill_holes_bpy  # noqa: E402

# Categorias shell/oca: strip cascas internas por defeito (sem fecho de chão).
_HOLLOW_SHELL_CATEGORIES = frozenset(
    {
        "building",
        "chapel",
        "house",
        "tower",
        "environment",
    }
)


def _is_hollow_shell_category(category: str | None) -> bool:
    if not category:
        return False
    return str(category).strip().lower() in _HOLLOW_SHELL_CATEGORIES


def _repair_topology_arrays_phase(
    mesh_obj,
    *,
    remove_internal_shells: bool,
    morph_close: float | None,
) -> dict:
    """Fase de filtro do ``topology_clean`` em arrays (numpy/scipy) + morph-close.

    Ordem **nova** face ao caminho bmesh: welds primeiro (mesh conexo, ~6x
    menos vértices sem os splits de normal) e morph-close **depois** — o
    solidify deixa de criar paredes por-triângulo em mesh split e o VDB passa
    a ver volume (casco exterior em vez de 2 shells preservadas).
    """
    log = logging.getLogger(__name__)
    from gamedev_shared.mesh_repair_arrays import (
        extract_arrays,
        repair_arrays_topology_clean,
        replace_mesh_arrays,
    )

    co, tris = extract_arrays(mesh_obj)
    co, tris, stats = repair_arrays_topology_clean(
        co,
        tris,
        do_remove_internal_shells=remove_internal_shells,
    )
    replace_mesh_arrays(mesh_obj, co, tris)

    if morph_close is not None and morph_close > 0:
        from gamedev_shared.mesh_repair import morphological_close

        mc_stats = morphological_close(mesh_obj, distance=float(morph_close))
        log.info(
            "morph-close (pós-weld): %s→%s faces (voxel=%.3f)",
            mc_stats.get("faces_before"),
            mc_stats.get("faces_after"),
            mc_stats.get("voxel_size", 0.0),
        )
    return stats


def _prepare_topology_bpy(
    mesh_obj,
    fill_holes_sides: int | None = None,
    *,
    watertight: bool | None = None,
    morph_close: float | None = None,
    remove_internal_shells: bool | None = None,
    category: str | None = None,
    engine: str = "arrays",
    has_armature: bool = False,
) -> None:
    """Pipeline de preparação de topologia — perfil Shared ``topology_clean``.

    Shade-smooth fica no Text3D (export GLTF / V/Tri). Reweld + fill + watertight
    seletivo no perfil. ``fill_holes_sides=None`` → valor do perfil (64).
    ``morph_close`` (metros) ativa fecho morfológico volumétrico antes do
    perfil: funde double shells finas e rachas MC que "dobram para dentro"
    (boundary=0 mas visualmente abertas). Destrói UVs — só pré-paint.

    Edifícios (``building``/``chapel``…): strip de cascas internas por defeito.

    ``engine``: ``arrays`` (default) / ``auto`` usam a fase vetorizada
    (:mod:`gamedev_shared.mesh_repair_arrays`) quando o mesh é seguro (sem
    UVs/weights/shape-keys/armature — ver :func:`arrays_engine_ok`); os passos
    topológicos (fill/caps/watertight/normais) correm sempre em bmesh.
    ``bpy`` força o caminho legado completo.
    """
    log = logging.getLogger(__name__)

    n_faces_before = len(mesh_obj.data.polygons)
    n_verts_before = len(mesh_obj.data.vertices)

    from gamedev_shared.mesh_repair import repair_mesh_object_with_profile
    from gamedev_shared.mesh_repair_arrays import arrays_engine_ok

    hollow = _is_hollow_shell_category(category)
    if remove_internal_shells is None:
        remove_internal_shells = hollow

    arrays_ok = arrays_engine_ok(mesh_obj, has_armature=has_armature)
    use_arrays = engine in ("auto", "arrays") and arrays_ok
    if engine == "arrays" and not arrays_ok:
        log.warning("engine=arrays pedido, mas o mesh tem UVs/weights/shape-keys/armature — fallback bpy")

    overrides: dict = {}
    if fill_holes_sides is not None:
        overrides["fill_holes_sides"] = int(fill_holes_sides)
    if watertight is not None:
        overrides["watertight"] = bool(watertight)

    if use_arrays:
        stats = _repair_topology_arrays_phase(
            mesh_obj,
            remove_internal_shells=bool(remove_internal_shells),
            morph_close=morph_close,
        )
        # Filtros já feitos em arrays — restam os passos topológicos do perfil.
        stats.update(
            repair_mesh_object_with_profile(
                mesh_obj,
                "topology_clean",
                do_sanitize=False,
                do_reweld_coincident=False,
                do_exact_weld=False,
                weld_mode="none",
                do_dissolve_loose=False,
                do_long_edges=False,
                sliver_max_aspect=None,
                debris_face_ratio=0.0,
                do_remove_internal_shells=False,
                **overrides,
            )
        )
    else:
        if morph_close is not None and morph_close > 0:
            from gamedev_shared.mesh_repair import morphological_close

            mc_stats = morphological_close(mesh_obj, distance=float(morph_close))
            log.info(
                "morph-close: %s→%s faces (voxel=%.3f)",
                mc_stats.get("faces_before"),
                mc_stats.get("faces_after"),
                mc_stats.get("voxel_size", 0.0),
            )

        if remove_internal_shells:
            overrides["do_remove_internal_shells"] = True

        stats = repair_mesh_object_with_profile(mesh_obj, "topology_clean", **overrides)
    if stats.get("boundary_before") is not None or stats.get("boundary_after") is not None:
        log.info(
            "prepare_topology watertight: boundary %s→%s (loops_capped=%s)",
            stats.get("boundary_before"),
            stats.get("boundary_after"),
            stats.get("loops_capped"),
        )
    if stats.get("internal_shell_faces"):
        log.info("remove_internal_shells: %s faces", stats.get("internal_shell_faces"))

    if stats.get("nonfinite_verts"):
        log.warning("Guard anti-NaN: %d vértices não-finitos removidos", stats["nonfinite_verts"])
    if stats.get("rewelded_coincident"):
        log.info("Reweld coincident (normal-split): %d vértices", stats["rewelded_coincident"])
    if stats.get("long_edge_faces"):
        log.warning("Leque/arestas outlier: %d faces removidas", stats["long_edge_faces"])
    if stats.get("sliver_faces"):
        log.warning("Slivers (agulhas): %d faces removidas", stats["sliver_faces"])
    if stats.get("debris_faces"):
        log.info("Debris removido: %d faces em ilhas soltas minúsculas", stats["debris_faces"])

    _shade_smooth(mesh_obj)

    log.info(
        "prepare_topology: %d→%d faces, %d→%d vértices",
        n_faces_before,
        len(mesh_obj.data.polygons),
        n_verts_before,
        len(mesh_obj.data.vertices),
    )


def _decimate_to_target(mesh_obj, target_faces: int) -> int:
    import bpy

    current = len(mesh_obj.data.polygons)
    if current <= target_faces:
        return current
    t = max(4, min(int(target_faces), current - 1))
    if t >= current:
        return current

    bpy.context.view_layer.objects.active = mesh_obj

    while len(mesh_obj.data.polygons) > t:
        current = len(mesh_obj.data.polygons)
        mod = mesh_obj.modifiers.new("Decimate", "DECIMATE")
        mod.decimate_type = "COLLAPSE"
        mod.ratio = max(0.01, t / current)
        mod.use_symmetry = False
        mod.use_collapse_triangulate = True
        bpy.ops.object.modifier_apply(modifier=mod.name)
        if len(mesh_obj.data.polygons) == current:
            break

    _shade_smooth(mesh_obj)

    return len(mesh_obj.data.polygons)


# ---------------------------------------------------------------------------
# Public API — path-based (GLB → GLB)
# ---------------------------------------------------------------------------


def scale_mesh_object_to_meters(mesh_obj, size_m: list[float] | tuple[float, ...] | None) -> float | None:
    """Escala verts do bpy object para metros reais de ``size_m`` (eixo maior).

    No-op se ``size_m`` ausente ou mesh já em escala (factor ≈ 1). Devolve o
    factor aplicado ou ``None``.
    """
    if not size_m:
        return None
    from text3d.bbox_tune import scale_factor_to_meters

    cur = float(max(mesh_obj.dimensions))
    factor = scale_factor_to_meters(cur, list(size_m))
    if factor is None:
        return None
    import numpy as np

    me = mesh_obj.data
    co = np.empty(len(me.vertices) * 3, dtype=np.float64)
    me.vertices.foreach_get("co", co)
    co *= factor
    me.vertices.foreach_set("co", co)
    me.update()
    logging.getLogger(__name__).info("scale-to-meters: max_dim %.3f → %.3f (factor %.3f)", cur, cur * factor, factor)
    return factor


def prepare_mesh_topology(
    input_path: Path | str,
    output_path: Path | str | None = None,
    *,
    fill_holes_sides: int | None = None,
    watertight: bool | None = None,
    morph_close: float | None = None,
    size_m: list[float] | tuple[float, ...] | None = None,
    remove_internal_shells: bool | None = None,
    category: str | None = None,
    engine: str = "arrays",
    **_legacy: object,
) -> Path:
    """Prepara topologia de um GLB: weld, fill holes, watertight seletivo.

    Carrega o GLB via bpy, aplica o pipeline de reparo e exporta. Preserva
    armatures, skins e animações quando presentes. Se bpy não estiver
    disponível, devolve o input inalterado com um warning.

    Args:
        input_path: GLB de entrada (ou objeto trimesh — backward compat).
        output_path: GLB de saída (se None, sobrepõe o ficheiro de entrada).
        fill_holes_sides: Tamanho máximo (em arestas) de buracos a preencher.
            ``None`` usa o perfil ``topology_clean`` (64). ``0`` desativa.
        watertight: Override do perfil (``None`` → perfil; tipicamente ``True``
            com skip_flap_erode + limite de diâmetro de loop).
        morph_close: Distância (m) do fecho morfológico volumétrico (dilate→
            erode via voxel remesh). Funde double shells/rachas dobradas para
            dentro. Destrói UVs — só pré-paint. ``None``/``0`` desliga.
        size_m: ``[L,H,W]`` metros reais — escala a mesh (unidades Omni ~2u →
            metros) antes do reparo, para que distâncias métricas (morph,
            weld) façam sentido físico. No-op se já em escala.
        remove_internal_shells: Strip cascas internas. ``None`` → auto building.
        category: Categoria do asset (hints de fecho de shell).
        engine: ``auto``/``arrays`` ativa a fase vetorizada (numpy/scipy) nos
            passos de filtro quando o mesh não tem UVs/weights/shape-keys/
            armature (10-100x mais rápida em meshes grandes, mesma semântica);
            ``bpy`` força o caminho legado completo.
        **_legacy: Aceita kwargs mortos (``skip_remesh``, ``force_close_base``)
            por compat — ignorados.

    Returns:
        Path para o GLB preparado (ou trimesh.Trimesh se input for trimesh).
    """
    log = logging.getLogger(__name__)
    if "skip_remesh" in _legacy:
        log.warning("prepare_mesh_topology: kwarg 'skip_remesh' obsoleto — ignorado")
    _was_trimesh = hasattr(input_path, "export")
    try:
        return _prepare_mesh_topology_impl(
            input_path,
            output_path,
            _was_trimesh,
            fill_holes_sides,
            watertight=watertight,
            morph_close=morph_close,
            size_m=size_m,
            remove_internal_shells=remove_internal_shells,
            category=category,
            engine=engine,
        )
    except ImportError:
        log.warning("bpy indisponível — prepare_mesh_topology ignorado (mesh NÃO foi reparada)")
        if _was_trimesh:
            return input_path
        return Path(input_path)


def _prepare_mesh_topology_impl(
    input_path,
    output_path,
    _was_trimesh,
    fill_holes_sides: int | None = None,
    *,
    watertight: bool | None = None,
    morph_close: float | None = None,
    size_m: list[float] | tuple[float, ...] | None = None,
    remove_internal_shells: bool | None = None,
    category: str | None = None,
    engine: str = "arrays",
):
    if _was_trimesh:
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".glb", delete=False) as tf:
            tf.close()
            tmp = Path(tf.name)
        input_path.export(str(tmp), file_type="glb")
        _input = tmp
        _output = Path(output_path) if output_path else tmp
    else:
        _input = Path(input_path)
        _output = Path(output_path) if output_path else _input

    mesh_obj, arm_objs = _load_glb_with_armatures(_input)
    scale_mesh_object_to_meters(mesh_obj, size_m)
    _prepare_topology_bpy(
        mesh_obj,
        fill_holes_sides=fill_holes_sides,
        watertight=watertight,
        morph_close=morph_close,
        remove_internal_shells=remove_internal_shells,
        category=category,
        engine=engine,
        has_armature=bool(arm_objs),
    )
    # Sem normal-split no export: reabre boundary no reimport (paint/LOD).
    # Shade-smooth já aplicado; importer/jogo recalcula normais se preciso.
    _export_glb(_output, mesh_obj, arm_objs, export_normals=False)

    if _was_trimesh:
        import trimesh

        return trimesh.load(str(_output), force="mesh")
    return _output


def pymeshfix_mesh_repair_only(input_path: Path | str, output_path: Path | str | None = None) -> Path:
    """Preenche buracos pequenos (≤30 lados) via bpy.ops.mesh.fill_holes."""
    input_path = Path(input_path)
    output_path = Path(output_path) if output_path else input_path
    try:
        mesh_obj, arm_objs = _load_glb_with_armatures(input_path)
        _fill_holes_bpy(mesh_obj, sides=30)
        _export_glb(output_path, mesh_obj, arm_objs)
    except ImportError:
        logging.getLogger(__name__).warning("bpy indisponível — pymeshfix_mesh_repair_only ignorado")
        return input_path
    return output_path


def apply_lod_meshfix(input_path: Path | str, output_path: Path | str | None = None) -> Path:
    """Opcional: só ``fill_small_boundaries`` (agora via bpy fill_holes)."""
    return pymeshfix_mesh_repair_only(input_path, output_path)


def simplify_to_face_count(input_path: Path | str, target_faces: int, output_path: Path | str | None = None) -> Path:
    """Quadric edge collapse via bpy Decimate modifier. Preserva armatures/skins/animações."""
    input_path = Path(input_path)
    output_path = Path(output_path) if output_path else input_path
    try:
        mesh_obj, arm_objs = _load_glb_with_armatures(input_path)
    except ImportError:
        logging.getLogger(__name__).warning("bpy indisponível — simplify_to_face_count ignorado")
        return input_path
    n = len(mesh_obj.data.polygons)
    if n < 4:
        raise ValueError(f"Mesh com poucas faces ({n}); simplificação não aplicável.")
    _decimate_to_target(mesh_obj, target_faces)
    _export_glb(output_path, mesh_obj, arm_objs)
    return output_path


def generate_lod_glb_triplet(
    input_path: Path,
    output_dir: Path,
    basename: str,
    *,
    lod1_ratio: float = 0.42,
    lod2_ratio: float = 0.14,
    min_faces_lod1: int = 500,
    min_faces_lod2: int = 150,
    meshfix: bool = False,
) -> list[Path]:
    """Gera três GLB: ``{basename}_lod0.glb`` … ``{basename}_lod2.glb``.

    Pipeline completo via bpy que preserva armatures, skin weights e
    animações em todos os níveis. Se bpy não estiver disponível,
    devolve lista vazia com warning.
    """
    if not 0 < lod2_ratio < lod1_ratio <= 1.0:
        raise ValueError("Esperado 0 < lod2_ratio < lod1_ratio <= 1.0")

    input_path = Path(input_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        return _generate_lod_glb_triplet_impl(
            input_path,
            output_dir,
            basename,
            lod1_ratio,
            lod2_ratio,
            min_faces_lod1,
            min_faces_lod2,
            meshfix,
        )
    except ImportError:
        logging.getLogger(__name__).warning("bpy indisponível — generate_lod_glb_triplet ignorado")
        return []


def _generate_lod_glb_triplet_impl(
    input_path,
    output_dir,
    basename,
    lod1_ratio,
    lod2_ratio,
    min_faces_lod1,
    min_faces_lod2,
    meshfix,
):
    mesh_obj, arm_objs = _load_glb_with_armatures(input_path)
    n = len(mesh_obj.data.polygons)
    if n < 4:
        raise ValueError(f"Mesh com poucas faces ({n}); LOD não aplicável.")
    if meshfix:
        _fill_holes_bpy(mesh_obj, sides=30)
    # Anti V/Tri=3: inputs sem NORMAL (ou flat) re-exportavam normais per-face
    # e lod0 herdava a explosão de vértices (decimate por nível já suavizava).
    _shade_smooth(mesh_obj)
    lod0_path = output_dir / f"{basename}_lod0.glb"
    _export_glb(lod0_path, mesh_obj, arm_objs)
    out_paths: list[Path] = [lod0_path]
    target_lod1 = max(min_faces_lod1, int(n * lod1_ratio))
    target_lod2 = max(min_faces_lod2, int(n * lod2_ratio))
    for level, target in ((1, target_lod1), (2, target_lod2)):
        mesh_obj_l, arm_objs_l = _load_glb_with_armatures(lod0_path)
        _decimate_to_target(mesh_obj_l, target)
        if meshfix:
            _fill_holes_bpy(mesh_obj_l, sides=30)
        path = output_dir / f"{basename}_lod{level}.glb"
        _export_glb(path, mesh_obj_l, arm_objs_l)
        out_paths.append(path)
    return out_paths


def generate_lod_textured_glb_triplet(
    painted_path: Path,
    output_dir: Path,
    basename: str,
    *,
    lod1_ratio: float = 0.42,
    lod2_ratio: float = 0.14,
    min_faces_lod1: int = 500,
    min_faces_lod2: int = 150,
    texture_size_lod0: int = 2048,
    target_faces: int | None = None,
    apply_finish: bool = True,
    finish_lod0: bool = False,
    apply_meshopt: bool = False,
    skin_source: Path | None = None,
    animation_source: Path | None = None,
) -> list[Path]:
    """Gera três GLB texturizados por decimação com preservação de UV.

    Se ``target_faces`` for dado: LOD0 = target_faces, LOD1 = target/2, LOD2 = target/4.
    Caso contrário usa ``lod1_ratio`` e ``lod2_ratio`` sobre o original.

    ``apply_meshopt`` controla EXT_meshopt_compression (desactivado por defeito;
    a quantização pode deslocar origem e inverter orientação nalguns viewers).

    ``skin_source``: GLB rigged (weights + skeleton). Após cada LOD, rebind via
    ``gamedev_shared.skin_transfer`` (KDTree weights + armature + anims).
    ``animation_source``: GLB com clips quando ``skin_source`` não tem animações
    (ex.: ``rigged_hi`` + ``*_animated.glb``).
    """
    from text3d.utils.mesh_remesh_textured import _clamp_decimate_target, remesh_textured_glb

    painted = Path(painted_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    import bpy

    from gamedev_shared.bpy_mesh import clear_scene

    clear_scene()
    bpy.ops.import_scene.gltf(filepath=str(painted))
    mesh_objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    model = max(mesh_objs, key=lambda o: len(o.data.polygons))
    n = len(model.data.polygons)

    tex_base = texture_size_lod0
    if model.data.materials and model.data.materials[0].use_nodes:
        for node in model.data.materials[0].node_tree.nodes:
            if node.type == "TEX_IMAGE" and node.image:
                # Respeitar texture_size_lod0 explícito (default 2048) em vez
                # de herdar o tamanho da imagem do paint3d (que pode ser 3072).
                # Só usar o tamanho da imagem se for menor que o pedido (upscale
                # nunca é desejável; downscale para o pedido é o objetivo).
                img_size = max(node.image.size[0], node.image.size[1])
                tex_base = min(texture_size_lod0, img_size)
                break
    clear_scene()

    if target_faces and target_faces > 0:
        lod0_target = _clamp_decimate_target(n, target_faces)
        lod1_target = _clamp_decimate_target(n, max(min_faces_lod1, lod0_target // 2))
        lod2_target = _clamp_decimate_target(n, max(min_faces_lod2, lod0_target // 4))
    else:
        lod0_target = n
        lod1_target = _clamp_decimate_target(n, max(min_faces_lod1, int(n * lod1_ratio)))
        lod2_target = _clamp_decimate_target(n, max(min_faces_lod2, int(n * lod2_ratio)))

    out_paths: list[Path] = []

    lod0_path = output_dir / f"{basename}_lod0.glb"
    if lod0_target < n:
        remesh_textured_glb(painted, lod0_path, target_faces=lod0_target, texture_size=tex_base)
    else:
        # painted já é LOD0 (mesmo path, hardlink ou inode partilhado) —
        # skip copy; resolve()!=samefile em hardlinks.
        import shutil

        same = False
        with contextlib.suppress(OSError):
            same = lod0_path.exists() and Path(painted).samefile(lod0_path)
        if not same and Path(painted).resolve() != lod0_path.resolve():
            shutil.copy2(painted, lod0_path)
    out_paths.append(lod0_path)

    for level, target, tex_size in (
        (1, lod1_target, max(64, tex_base // 2)),
        (2, lod2_target, max(32, tex_base // 4)),
    ):
        path = output_dir / f"{basename}_lod{level}.glb"
        remesh_textured_glb(painted, path, target_faces=target, texture_size=tex_size)
        out_paths.append(path)

    if apply_finish:
        from .gltf_finish import gltf_transform_finish

        finish_targets = out_paths if finish_lod0 else out_paths[1:]
        for p in finish_targets:
            res = gltf_transform_finish(p, p, apply_meshopt=apply_meshopt)
            if not res.fully_optimized():
                log.warning(
                    "gltf_finish não optimizou totalmente %s: ktx2=%s meshopt=%s skipped='%s'",
                    p.name,
                    res.ktx2_applied,
                    res.meshopt_applied,
                    res.skipped_reason,
                )

    # Rebind skin/skeleton/animations onto painted/decimated topology.
    if skin_source is not None and Path(skin_source).is_file():
        from gamedev_shared.skin_transfer import transfer_skin_to_mesh

        skin_src = Path(skin_source)
        anim_src = Path(animation_source) if animation_source else None
        for p in out_paths:
            if not p.is_file():
                continue
            tmp = p.with_suffix(".skin_tmp.glb")
            try:
                result = transfer_skin_to_mesh(
                    skin_src,
                    p,
                    tmp,
                    animation_source=anim_src,
                )
                tmp.replace(p)
                log.info(
                    "LOD skin_transfer %s: bones=%d vgroups=%d anims=%d weights=%d",
                    p.name,
                    result.bones,
                    result.vertex_groups,
                    result.animations,
                    result.weights_assigned,
                )
            except Exception as exc:
                log.error("LOD skin_transfer falhou em %s: %s", p.name, exc)
                with contextlib.suppress(OSError):
                    tmp.unlink(missing_ok=True)
                raise

    if out_paths and out_paths[0].is_file():
        lod0_size = out_paths[0].stat().st_size
        for p in out_paths[1:]:
            if p.is_file() and p.stat().st_size > lod0_size:
                log.warning(
                    "LOD regressão: %s (%d bytes) > LOD0 %s (%d bytes)",
                    p.name,
                    p.stat().st_size,
                    out_paths[0].name,
                    lod0_size,
                )

    return out_paths
