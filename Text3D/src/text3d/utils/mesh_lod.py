"""Geração de variantes LOD (níveis de detalhe) de meshes GLB via bpy.

Cada função opera sobre ficheiros GLB e preserva armatures, skin weights e
animações quando presentes no input.
"""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path

from aigamekit_shared.bpy_mesh import clear_scene

log = logging.getLogger(__name__)

# Nível de LOD mais distante que ainda carrega rig. LOD0/LOD1 animam; o
# LOD2 (o nível mais longe) sai como mesh estático — ver `strip_rig_in_scene`
# para o custo em runtime de esqueletos que ninguém vê animar.
DEFAULT_RIG_MAX_LEVEL = 1

LOD_LEVELS = (0, 1, 2)


def lod_levels_with_rig(rig_max_level: int = DEFAULT_RIG_MAX_LEVEL) -> tuple[int, ...]:
    """Níveis da ladder que mantêm skin/esqueleto.

    Acima do limite o entregável é mesh estático: um esqueleto por nível custa
    no runtime mesmo escondido (ver :func:`strip_rig_in_scene`).
    """
    return tuple(level for level in LOD_LEVELS if level <= int(rig_max_level))


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


def _scale_object_textures(mesh_obj, texture_size: int) -> None:
    """Downscale todas as ``TEX_IMAGE`` do mesh para ``texture_size``x``texture_size``."""
    if texture_size is None or int(texture_size) <= 0:
        return
    from text3d.utils.mesh_remesh_textured import downscale_image_replace

    size = int(texture_size)
    for mat_slot in mesh_obj.material_slots:
        mat = mat_slot.material
        if not mat or not mat.use_nodes:
            continue
        for node in mat.node_tree.nodes:
            if node.type != "TEX_IMAGE" or not node.image:
                continue
            downscale_image_replace(node, size)


def _shade_smooth(mesh_obj) -> None:
    """Marca faces como smooth-shaded, mantendo arestas duras só acima de 60°.

    Substitui o antigo ``_restore_smooth_normals`` que aplicava
    ``normals_split_custom_set(loop_normals)``. Esse caminho fazia o exporter
    GLTF do Blender escrever normais por canto de face, duplicando vértices
    (V/Tri ≈ 3 — bug do goblin_shape).

    Usa ``aigamekit_shared.bpy_mesh.apply_smooth_by_angle`` (60°), que funciona no
    bpy 5.x via ``shade_smooth_by_angle`` — o antigo ``use_auto_smooth`` foi
    removido no Blender 4.1 e era silenciosamente ignorado, deixando a malha
    totalmente lisa.
    """
    from aigamekit_shared.bpy_mesh import apply_smooth_by_angle

    mesh = mesh_obj.data
    with contextlib.suppress(Exception):
        # Limpa qualquer custom split normal data que possa ter ficado da geração
        mesh.free_normals_split()
    apply_smooth_by_angle(mesh_obj, 60.0)


def _dynamic_weld_distance(vertex_count: int) -> float:
    """Compat: delega em ``aigamekit_shared.mesh_repair.dynamic_weld_distance``."""
    from aigamekit_shared.mesh_repair import dynamic_weld_distance

    return dynamic_weld_distance(vertex_count)


def _load_glb_with_armatures(path: Path) -> tuple:
    """Importa GLB via bpy, devolve (mesh_obj, armature_objs).

    Limpa a cena antes de importar. Preserva transforms, armatures,
    shape keys e materiais. Meshopt-aware: em bpy < 5.2 descomprime antes
    via ``gltf-transform`` (``bpy_readable_glb``) — necessário para gerar a
    ladder a partir de um lod0 já finalizado com EXT_meshopt_compression.
    """
    import bpy

    from aigamekit_shared.skin_transfer import bpy_readable_glb

    path = Path(path).expanduser().resolve()
    clear_scene()
    with bpy_readable_glb(path) as import_path:
        from aigamekit_shared.bpy_mesh import import_gltf

        import_gltf(import_path)
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
    Com ``export_normals=True`` aplica ``smooth_shade_scene`` antes — senão
    import flat → V/Tri≈3 no entregável.
    """
    import bpy

    from aigamekit_shared.bpy_mesh import smooth_shade_scene

    if export_normals:
        smooth_shade_scene([mesh_obj])
        if mesh_obj.data.uv_layers:
            with contextlib.suppress(Exception):
                mesh_obj.data.calc_tangents()

    export_objects = [mesh_obj, *arm_objs]
    bpy.ops.object.select_all(action="DESELECT")
    for o in export_objects:
        o.select_set(True)
    bpy.context.view_layer.objects.active = arm_objs[0] if arm_objs else mesh_obj
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    has_armature = bool(arm_objs)
    if export_tangents is None:
        export_tangents = bool(export_normals) and bool(mesh_obj.data.uv_layers)
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
        # JPEG: AUTO + downscale PNG fazia lod1 (1024 PNG) > lod0 (2048 JPEG).
        export_image_format="JPEG",
    )
    with contextlib.suppress(Exception):
        from aigamekit_shared.glb_verify import post_save_verify

        post_save_verify(
            output_path,
            require_normals=export_normals if export_normals else False,
        )


_export_glb = _export_textured_glb


# Primitivas de reparação unificadas em aigamekit_shared.mesh_repair.
from aigamekit_shared.mesh_repair import fill_holes as _fill_holes_bpy  # noqa: E402

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
    from aigamekit_shared.mesh_repair_arrays import (
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
        from aigamekit_shared.mesh_repair import morphological_close

        mc_stats = morphological_close(mesh_obj, distance=float(morph_close))
        log.info(
            "morph-close (pós-weld): %s→%s faces (voxel=%.3f)%s",
            mc_stats.get("faces_before"),
            mc_stats.get("faces_after"),
            mc_stats.get("voxel_size", 0.0),
            " — REVERTIDO (colapso de volume)" if mc_stats.get("reverted_volume_collapse") else "",
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
    (:mod:`aigamekit_shared.mesh_repair_arrays`) quando o mesh é seguro (sem
    UVs/weights/shape-keys/armature — ver :func:`arrays_engine_ok`); os passos
    topológicos (fill/caps/watertight/normais) correm sempre em bmesh.
    ``bpy`` força o caminho legado completo.
    """
    log = logging.getLogger(__name__)

    n_faces_before = len(mesh_obj.data.polygons)
    n_verts_before = len(mesh_obj.data.vertices)

    from aigamekit_shared.mesh_repair import repair_mesh_object_with_profile
    from aigamekit_shared.mesh_repair_arrays import arrays_engine_ok

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
        from aigamekit_shared.mesh_repair_arrays import extract_arrays, replace_mesh_arrays

        backup_co, backup_tris = extract_arrays(mesh_obj)
        stats = _repair_topology_arrays_phase(
            mesh_obj,
            remove_internal_shells=bool(remove_internal_shells),
            morph_close=morph_close,
        )
        if n_faces_before > 0 and len(mesh_obj.data.polygons) == 0:
            log.warning(
                "engine=arrays zerou mesh (%d→0 faces) — restaurar + fallback bpy",
                n_faces_before,
            )
            replace_mesh_arrays(mesh_obj, backup_co, backup_tris)
            use_arrays = False
        else:
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

    if not use_arrays:
        if morph_close is not None and morph_close > 0:
            from aigamekit_shared.mesh_repair import morphological_close

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
        log.info("Slivers (agulhas): %d faces colapsadas", stats["sliver_faces"])
    if stats.get("duplicate_faces"):
        log.info("Faces sobrepostas removidas (pós-weld): %d", stats["duplicate_faces"])
    for key, val in stats.items():
        if key.endswith("_reverted") and val:
            log.warning("Reparo revertido (%s): %d faces abririam a malha fechada", key[:-9], val)
    if stats.get("debris_faces"):
        log.info("Debris removido: %d faces em ilhas soltas minúsculas", stats["debris_faces"])

    _shade_smooth(mesh_obj)

    n_faces_after = len(mesh_obj.data.polygons)
    n_verts_after = len(mesh_obj.data.vertices)
    log.info(
        "prepare_topology: %d→%d faces, %d→%d vértices",
        n_faces_before,
        n_faces_after,
        n_verts_before,
        n_verts_after,
    )
    if n_faces_before > 0 and n_faces_after == 0:
        raise RuntimeError(
            f"prepare_topology zerou a malha ({n_verts_before}→{n_verts_after} verts, "
            f"{n_faces_before}→0 faces) — abortar export de clean vazio"
        )


def _decimate_to_target(mesh_obj, target_faces: int) -> int:
    """Decimate COLLAPSE até ``target_faces``.

    Weld micro-duplicados primeiro: GLBs rigged/animated com V/Tri≈3 (verts
    já partidos no ficheiro — ``smooth_shade`` sozinho NÃO funde) fazem o
    Decimate colapsar triângulos isolados → LOD1/2 “moth-eaten”.
    """
    from aigamekit_shared.mesh_repair import repair_mesh_object_with_profile
    from aigamekit_shared.mesh_simplify import simplify_mesh_object

    # Sem pre_decimate_uv / weld pré-COLLAPSE — travam o rácio (~20k piso).
    # Post-clean depois do Decimate stepwise.
    stats = simplify_mesh_object(mesh_obj, target_faces, repair=False)
    repair_mesh_object_with_profile(mesh_obj, "post_decimate")
    return int(stats.get("faces_after", len(mesh_obj.data.polygons)))


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

    try:
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
        # Smooth vertex normals (não omitir NORMAL): export_normals=False deixava
        # viewers flat e o save_mesh a seguir reintroduzia V/Tri≈3 faceted.
        # smooth_shade_scene dentro do export evita split duro / boundary reopen.
        _export_glb(_output, mesh_obj, arm_objs, export_normals=True)
    except BaseException:
        if _was_trimesh:
            tmp.unlink(missing_ok=True)
        raise

    if _was_trimesh:
        import trimesh

        # Carregar ANTES de apagar o tmp — quando output_path é None, o
        # próprio tmp é o output (_output == tmp).
        loaded = trimesh.load(str(_output), force="mesh")
        # O GLB temporário do trimesh nunca era apagado — cada chamada batch
        # deixava um ficheiro lixo em /tmp.
        tmp.unlink(missing_ok=True)
        return loaded
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
    texture_size_lod0: int | None = None,
    target_faces: int | None = None,
    rig_max_level: int = DEFAULT_RIG_MAX_LEVEL,
) -> list[Path]:
    """Gera três GLB: ``{basename}_lod0.glb`` … ``{basename}_lod2.glb``.

    Preferência: ``gltf-transform simplify`` (meshoptimizer) — costuras UV e
    de skin (`JOINTS`/`WEIGHTS`) ficam bloqueadas, atlas intacto. No **piso de
    costuras** (alvo inatingível sem rasgar) aceita faces acima do orçamento;
    Decimate COLLAPSE bpy só entra se o CLI faltar. Skinned: weld desligado
    (fundir verts com weights diferentes corrompe o rig).

    Preserva armatures, skin weights e animações. Sem bpy → lista vazia.

    ``texture_size_lod0``: downscale atlas (lod1=/2, lod2=/4, snap64).
    ``target_faces``: se dado, LOD0 tenta este alvo; LOD1/2 usam ratios sobre
    o LOD0 efectivo (com mínimos).
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
            texture_size_lod0,
            target_faces,
            rig_max_level,
        )
    except ImportError:
        logging.getLogger(__name__).warning("bpy indisponível — generate_lod_glb_triplet ignorado")
        return []


def _meshopt_simplify_level(
    src: Path,
    dst: Path,
    target_faces: int,
    *,
    weld: bool,
) -> int | None:
    """Simplifica com meshoptimizer. Aceita piso de costuras (faces ≥ alvo).

    Returns:
        Contagem de faces em ``dst``, ou ``None`` se o CLI estiver indisponível.
    """
    import shutil

    from .gltf_finish import glb_face_count, meshopt_simplify_glb

    n = glb_face_count(src)
    if n <= 0:
        return None
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if n <= int(target_faces):
        if Path(src).resolve() != dst.resolve():
            shutil.copy2(src, dst)
        return n
    ok, faces, err = meshopt_simplify_glb(src, dst, target_faces=int(target_faces), weld=weld)
    if not ok:
        log.info("LOD meshopt skip (%s) — cai para Decimate COLLAPSE", err)
        return None
    if faces > int(target_faces):
        log.info(
            "LOD meshopt piso de costuras: alvo %d → %d faces (aceite — COLLAPSE rasgaria UVs/weights)",
            int(target_faces),
            faces,
        )
    else:
        log.info("LOD meshopt: %d → %d faces (alvo %d)", n, faces, int(target_faces))
    return faces


def strip_rig_in_scene(mesh_obj, arm_objs: list) -> list:
    """Congela o mesh na pose actual e remove armature + skin data.

    Um LOD distante que carrega o seu próprio esqueleto custa no runtime muito
    mais do que a geometria que desenha: no VibeGame o browser percorre e
    recompõe todos os nós da cena por frame, visíveis ou não, e um prop rigado
    traz ~47 ossos **por nível** (simple-rpg: ~11k ossos, 12.3k de 15.4k nós da
    cena, quase todos em níveis de LOD que ninguém vê animar).

    Devolve a lista de armatures que sobrou (vazia) para o chamador exportar.
    """
    import bpy

    world = mesh_obj.matrix_world.copy()
    for mod in list(mesh_obj.modifiers):
        if mod.type == "ARMATURE":
            mesh_obj.modifiers.remove(mod)
    mesh_obj.vertex_groups.clear()
    if mesh_obj.parent is not None:
        mesh_obj.parent = None
    # Reparent/modifier removal deixa a matriz local a valer sozinha — repor a
    # world preserva a pose/escala que o LOD0 tem.
    mesh_obj.matrix_world = world
    for arm in list(arm_objs):
        with contextlib.suppress(Exception):
            bpy.data.objects.remove(arm, do_unlink=True)
    return []


def _finalize_geometric_lod(
    src: Path,
    dst: Path,
    *,
    texture_size: int | None,
    meshfix: bool,
    keep_rig: bool = True,
) -> None:
    """Importa o GLB simplificado, aplica downscale de atlas / meshfix, re-exporta."""
    mesh_obj, arm_objs = _load_glb_with_armatures(src)
    if meshfix:
        _fill_holes_bpy(mesh_obj, sides=30)
    if texture_size is not None and int(texture_size) > 0:
        _scale_object_textures(mesh_obj, int(texture_size))
    _shade_smooth(mesh_obj)
    if not keep_rig and arm_objs:
        arm_objs = strip_rig_in_scene(mesh_obj, arm_objs)
    _export_glb(dst, mesh_obj, arm_objs)


def _generate_lod_glb_triplet_impl(
    input_path,
    output_dir,
    basename,
    lod1_ratio,
    lod2_ratio,
    min_faces_lod1,
    min_faces_lod2,
    meshfix,
    texture_size_lod0,
    target_faces,
    rig_max_level=DEFAULT_RIG_MAX_LEVEL,
):
    import shutil
    import tempfile

    from aigamekit_shared.lod_budget import lod_texture_ladder

    from .gltf_finish import _glb_has_skins, glb_face_count

    input_path = Path(input_path)
    skinned = _glb_has_skins(input_path)
    # Skinned: nunca weld pré-simplify — funde verts com JOINTS/WEIGHTS diferentes.
    weld = not skinned
    n_source = glb_face_count(input_path)
    if n_source < 4:
        # Contagem JSON falhou — deixa o caminho bpy decidir / erro.
        return _generate_lod_glb_triplet_bpy_collapse(
            input_path,
            output_dir,
            basename,
            lod1_ratio,
            lod2_ratio,
            min_faces_lod1,
            min_faces_lod2,
            meshfix,
            texture_size_lod0,
            target_faces,
        )

    lod0_target = n_source
    if target_faces is not None and int(target_faces) > 0:
        lod0_target = min(n_source, int(target_faces))

    tex_ladder: tuple[int, int, int] | None = None
    if texture_size_lod0 is not None and int(texture_size_lod0) > 0:
        tex_ladder = lod_texture_ladder(int(texture_size_lod0))

    with tempfile.TemporaryDirectory(prefix="lod_meshopt_") as tmpdir:
        tmp = Path(tmpdir)
        raw0 = tmp / "raw_lod0.glb"
        faces0 = _meshopt_simplify_level(input_path, raw0, lod0_target, weld=weld)
        if faces0 is None:
            return _generate_lod_glb_triplet_bpy_collapse(
                input_path,
                output_dir,
                basename,
                lod1_ratio,
                lod2_ratio,
                min_faces_lod1,
                min_faces_lod2,
                meshfix,
                texture_size_lod0,
                target_faces,
            )

        lod0_path = Path(output_dir) / f"{basename}_lod0.glb"
        _finalize_geometric_lod(
            raw0,
            lod0_path,
            texture_size=tex_ladder[0] if tex_ladder else None,
            meshfix=meshfix,
        )
        faces0 = glb_face_count(lod0_path) or faces0
        out_paths: list[Path] = [lod0_path]

        target_lod1 = max(min_faces_lod1, int(faces0 * lod1_ratio))
        target_lod2 = max(min_faces_lod2, int(faces0 * lod2_ratio))
        prev_raw = raw0
        prev_faces = faces0
        for level, target in ((1, target_lod1), (2, target_lod2)):
            raw = tmp / f"raw_lod{level}.glb"
            if prev_faces <= target:
                shutil.copy2(prev_raw, raw)
                faces = prev_faces
            else:
                # Simplifica a partir do HI (melhor silhueta); se o piso ≥ lod
                # anterior, reutiliza o nível anterior.
                faces = _meshopt_simplify_level(input_path, raw, target, weld=weld)
                if faces is None:
                    log.warning(
                        "LOD meshopt falhou no lod%d — a completar ladder com COLLAPSE a partir do lod0",
                        level,
                    )
                    return _complete_ladder_bpy_from_lod0(
                        lod0_path,
                        output_dir,
                        basename,
                        out_paths,
                        start_level=level,
                        targets={1: target_lod1, 2: target_lod2},
                        meshfix=meshfix,
                        tex_ladder=tex_ladder,
                    )
                if faces >= int(prev_faces * 0.98):
                    shutil.copy2(prev_raw, raw)
                    faces = prev_faces
                    log.info(
                        "LOD meshopt lod%d no mesmo piso que lod%d (%d faces) — a reutilizar",
                        level,
                        level - 1,
                        faces,
                    )
            path = Path(output_dir) / f"{basename}_lod{level}.glb"
            _finalize_geometric_lod(
                raw,
                path,
                texture_size=tex_ladder[level] if tex_ladder else None,
                meshfix=meshfix,
                keep_rig=level <= rig_max_level,
            )
            out_paths.append(path)
            prev_raw, prev_faces = raw, faces
        return out_paths


def _complete_ladder_bpy_from_lod0(
    lod0_path: Path,
    output_dir: Path,
    basename: str,
    out_paths: list[Path],
    *,
    start_level: int,
    targets: dict[int, int],
    meshfix: bool,
    tex_ladder: tuple[int, int, int] | None,
    rig_max_level: int = DEFAULT_RIG_MAX_LEVEL,
) -> list[Path]:
    """Completa lod1/lod2 com Decimate COLLAPSE quando meshopt morre a meio."""
    for level in range(start_level, 3):
        mesh_obj_l, arm_objs_l = _load_glb_with_armatures(lod0_path)
        _decimate_to_target(mesh_obj_l, targets[level])
        if meshfix:
            _fill_holes_bpy(mesh_obj_l, sides=30)
        if tex_ladder is not None:
            _scale_object_textures(mesh_obj_l, tex_ladder[level])
        if level > rig_max_level and arm_objs_l:
            arm_objs_l = strip_rig_in_scene(mesh_obj_l, arm_objs_l)
        path = Path(output_dir) / f"{basename}_lod{level}.glb"
        _export_glb(path, mesh_obj_l, arm_objs_l)
        out_paths.append(path)
    return out_paths


def _generate_lod_glb_triplet_bpy_collapse(
    input_path,
    output_dir,
    basename,
    lod1_ratio,
    lod2_ratio,
    min_faces_lod1,
    min_faces_lod2,
    meshfix,
    texture_size_lod0,
    target_faces,
    rig_max_level=DEFAULT_RIG_MAX_LEVEL,
):
    """Fallback legado: Decimate COLLAPSE bpy (cego a costuras UV)."""
    from aigamekit_shared.lod_budget import lod_texture_ladder
    from aigamekit_shared.mesh_repair import remove_doubles

    mesh_obj, arm_objs = _load_glb_with_armatures(input_path)
    n = len(mesh_obj.data.polygons)
    if n < 4:
        raise ValueError(f"Mesh com poucas faces ({n}); LOD não aplicável.")
    if meshfix:
        _fill_holes_bpy(mesh_obj, sides=30)
    # Rigged/animated frequentemente chegam com verts já duplicados (V/Tri≈3).
    # smooth_shade NÃO funde — só remove_doubles. Sem weld, Decimate rasga.
    welded = remove_doubles(mesh_obj, threshold=0.0001)
    if welded:
        log.info("LOD geometry: welded %d duplicate verts (input V/Tri≈3)", welded)
    _shade_smooth(mesh_obj)
    n = len(mesh_obj.data.polygons)
    if target_faces is not None and int(target_faces) > 0 and int(target_faces) < n:
        _decimate_to_target(mesh_obj, int(target_faces))
        n = len(mesh_obj.data.polygons)
    tex_ladder: tuple[int, int, int] | None = None
    if texture_size_lod0 is not None and int(texture_size_lod0) > 0:
        tex_ladder = lod_texture_ladder(int(texture_size_lod0))
        _scale_object_textures(mesh_obj, tex_ladder[0])
    lod0_path = Path(output_dir) / f"{basename}_lod0.glb"
    _export_glb(lod0_path, mesh_obj, arm_objs)
    out_paths: list[Path] = [lod0_path]
    target_lod1 = max(min_faces_lod1, int(n * lod1_ratio))
    target_lod2 = max(min_faces_lod2, int(n * lod2_ratio))
    for level, target in ((1, target_lod1), (2, target_lod2)):
        mesh_obj_l, arm_objs_l = _load_glb_with_armatures(lod0_path)
        _decimate_to_target(mesh_obj_l, target)
        if meshfix:
            _fill_holes_bpy(mesh_obj_l, sides=30)
        if tex_ladder is not None:
            _scale_object_textures(mesh_obj_l, tex_ladder[level])
        if level > rig_max_level and arm_objs_l:
            arm_objs_l = strip_rig_in_scene(mesh_obj_l, arm_objs_l)
        path = Path(output_dir) / f"{basename}_lod{level}.glb"
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
    apply_meshopt: bool = True,
    skin_source: Path | None = None,
    animation_source: Path | None = None,
    rig_max_level: int = DEFAULT_RIG_MAX_LEVEL,
) -> list[Path]:
    """Gera três GLB texturizados por decimação com preservação de UV.

    Se ``target_faces`` for dado: LOD0 = target_faces, LOD1 = max(min1, lod0/2),
    LOD2 = max(min2, lod0/3). Caso contrário usa ``lod1_ratio`` / ``lod2_ratio``
    sobre o original.

    ``apply_meshopt`` controla EXT_meshopt_compression (activo por defeito;
    quantização POSITION SHORT via KHR_mesh_quantization — CLI gltf-transform 4.x).

    ``skin_source``: GLB rigged (weights + skeleton). Após cada LOD, rebind via
    ``aigamekit_shared.skin_transfer`` (KDTree weights + armature + anims).
    ``animation_source``: GLB com clips quando ``skin_source`` não tem animações
    (ex.: ``rigged_hi`` + ``*_animated.glb``).
    """
    from aigamekit_shared.lod_budget import lod_texture_ladder
    from text3d.utils.mesh_remesh_textured import _clamp_decimate_target, remesh_textured_glb

    painted = Path(painted_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    import bpy

    from aigamekit_shared.bpy_mesh import clear_scene

    clear_scene()
    from aigamekit_shared.bpy_mesh import import_gltf

    import_gltf(painted)
    mesh_objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not mesh_objs:
        # GLB só com câmaras/empties — max() vazio dava ValueError críptico.
        raise ValueError(f"No mesh objects found in {painted}")
    model = max(mesh_objs, key=lambda o: len(o.data.polygons))
    n = len(model.data.polygons)

    img_size = int(texture_size_lod0)
    if model.data.materials and model.data.materials[0].use_nodes:
        for node in model.data.materials[0].node_tree.nodes:
            if node.type == "TEX_IMAGE" and node.image:
                # Nunca upscale acima do painted; downscale para o pedido.
                img_size = min(int(texture_size_lod0), max(node.image.size[0], node.image.size[1]))
                break
    tex0, tex1, tex2 = lod_texture_ladder(img_size)
    clear_scene()

    if target_faces and target_faces > 0:
        lod0_target = _clamp_decimate_target(n, target_faces)
        lod1_target = _clamp_decimate_target(n, max(min_faces_lod1, lod0_target // 2))
        # /3 (não /4): edifícios/props grandes degeneram em silhueta no far LOD.
        lod2_target = _clamp_decimate_target(n, max(min_faces_lod2, lod0_target // 3))
    else:
        lod0_target = n
        lod1_target = _clamp_decimate_target(n, max(min_faces_lod1, int(n * lod1_ratio)))
        lod2_target = _clamp_decimate_target(n, max(min_faces_lod2, int(n * lod2_ratio)))

    # Ladder achatada = três meshes iguais em disco e em VRAM, sem ganho de LOD.
    # Acontecia quando o piso relativo de clamp_decimate_target subia lod1 e
    # lod2 ao mesmo valor (ver _MIN_DECIMATE_FRAC).
    if lod1_target == lod2_target or lod0_target == lod1_target:
        log.warning(
            "LOD ladder achatada: alvos lod0=%d lod1=%d lod2=%d (pedido %s) — níveis iguais não poupam nada",
            lod0_target,
            lod1_target,
            lod2_target,
            target_faces,
        )

    out_paths: list[Path] = []

    lod0_path = output_dir / f"{basename}_lod0.glb"
    # Sempre passar por remesh_textured (mesmo sem decimate) para forçar
    # downscale de atlas — copy2 do painted deixava lod0/1/2 com a mesma tex.
    remesh_textured_glb(painted, lod0_path, target_faces=lod0_target, texture_size=tex0)
    out_paths.append(lod0_path)

    for level, target, tex_size in (
        (1, lod1_target, tex1),
        (2, lod2_target, tex2),
    ):
        path = output_dir / f"{basename}_lod{level}.glb"
        remesh_textured_glb(painted, path, target_faces=target, texture_size=tex_size)
        out_paths.append(path)

    missing = [p for p in out_paths if not p.is_file() or p.stat().st_size < 64]
    if missing:
        names = ", ".join(p.name for p in missing)
        raise RuntimeError(f"LOD ladder incompleta após remesh (ausente/vazio): {names}")

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
        from aigamekit_shared.skin_transfer import transfer_skin_to_mesh

        skin_src = Path(skin_source)
        anim_src = Path(animation_source) if animation_source else None
        rigged_levels = lod_levels_with_rig(rig_max_level)
        for level, p in enumerate(out_paths):
            if not p.is_file():
                continue
            # Níveis acima de `rig_max_level` ficam estáticos: rebind punha o
            # esqueleto inteiro em cada nível, e o runtime paga esses ossos por
            # frame mesmo com o nível escondido (ver `strip_rig_in_scene`).
            if level not in rigged_levels:
                log.info("LOD skin_transfer ignorado em %s (rig_max_level=%d)", p.name, rig_max_level)
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
