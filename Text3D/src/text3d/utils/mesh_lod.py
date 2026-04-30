"""Geração de variantes LOD (níveis de detalhe) de meshes GLB via bpy.

Cada função opera sobre ficheiros GLB e preserva armatures, skin weights e
animações quando presentes no input.
"""

from __future__ import annotations

import logging
from pathlib import Path

from gamedev_shared.bpy_mesh import clear_scene

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


def _dynamic_weld_distance(vertex_count: int) -> float:
    """Distância de weld adaptativa baseada na densidade de vértices.

    Malhas mais densas (>150k vértices) usam thresholds menores para
    preservar detalhes; malhas leves (<50k) usam thresholds maiores
    para fechar rachaduras de marching cubes.
    """
    if vertex_count > 150_000:
        return 0.003
    if vertex_count > 100_000:
        return 0.005
    if vertex_count > 50_000:
        return 0.008
    return 0.01


def _load_glb_with_armatures(path: Path) -> tuple:
    """Importa GLB via bpy, devolve (mesh_obj, armature_objs).

    Limpa a cena antes de importar. Preserva transforms, armatures,
    shape keys e materiais.
    """
    import bpy

    path = Path(path).expanduser().resolve()
    clear_scene()
    bpy.ops.import_scene.gltf(filepath=str(path))
    mesh_objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not mesh_objs:
        raise ValueError(f"No mesh objects found in {path}")
    # Usa a mesh com mais polígonos (mesh principal)
    mesh_obj = max(mesh_objs, key=lambda o: len(o.data.polygons))
    arm_objs = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
    return mesh_obj, arm_objs


def _export_textured_glb(output_path: Path, mesh_obj, arm_objs: list) -> None:
    """Exporta mesh + texturas para GLB (geometry + materials + images)."""
    import bpy

    export_objects = [mesh_obj, *arm_objs]
    bpy.ops.object.select_all(action="DESELECT")
    for o in export_objects:
        o.select_set(True)
    bpy.context.view_layer.objects.active = arm_objs[0] if arm_objs else mesh_obj
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    has_armature = bool(arm_objs)
    bpy.ops.export_scene.gltf(
        filepath=str(output_path),
        export_format="GLB",
        use_selection=True,
        export_apply=True,
        export_animations=has_armature,
        export_skins=has_armature,
        export_all_influences=False,
        export_normals=True,
        export_texcoords=True,
        export_materials="EXPORT",
        export_image_format="AUTO",
    )


def _remove_doubles(obj, threshold: float) -> int:
    """Remove vértices duplicados dentro de *threshold*. Devolve vértices removidos."""
    import bpy

    before = len(obj.data.vertices)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.remove_doubles(threshold=threshold, use_sharp_edge_from_normals=True)
    bpy.ops.object.mode_set(mode="OBJECT")
    return before - len(obj.data.vertices)


def _fill_holes_bpy(obj, sides: int = 30) -> None:
    """Preenche buracos com até *sides* arestas via bpy.ops.mesh.fill_holes."""
    import bpy

    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="DESELECT")
    bpy.ops.mesh.select_non_manifold()
    bpy.ops.mesh.fill_holes(sides=sides)
    bpy.ops.object.mode_set(mode="OBJECT")


def _make_normals_consistent(obj) -> None:
    """Recalcula normais para ficarem consistentes (para fora)."""
    import bpy

    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.normals_make_consistent()
    bpy.ops.object.mode_set(mode="OBJECT")


def _prepare_topology_bpy(mesh_obj) -> None:
    """Pipeline de preparação de topologia sobre um bpy mesh object (in-place).

    1. Remove doubles exactos (threshold baixo)
    2. Weld por distância adaptativa (fecha micro-cracks)
    3. Normais consistentes
    4. Fill holes pequenos (≤30 lados)
    """
    log = logging.getLogger(__name__)

    n_faces_before = len(mesh_obj.data.polygons)
    n_verts_before = len(mesh_obj.data.vertices)

    # 1. Remove doubles exactos
    removed = _remove_doubles(mesh_obj, threshold=0.0001)
    if removed:
        log.info("Remove doubles exactos: %d vértices removidos", removed)

    # 2. Weld por distância adaptativa
    weld_dist = _dynamic_weld_distance(len(mesh_obj.data.vertices))
    removed = _remove_doubles(mesh_obj, threshold=weld_dist)
    if removed:
        log.info("Weld adaptativo (%.4f): %d vértices removidos", weld_dist, removed)

    # 3. Normais consistentes
    try:
        _make_normals_consistent(mesh_obj)
    except Exception as exc:
        log.warning("normals_make_consistent falhou: %s", exc)

    # 4. Fill holes pequenos
    try:
        _fill_holes_bpy(mesh_obj, sides=30)
    except Exception as exc:
        log.warning("fill_holes falhou: %s", exc)

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

    # Iteratively decimate using planar collapse — preserves shape much better
    # than COLLAPSE on thin/non-manifold geometry. Planar collapses faces that
    # lie on the same plane, preserving the overall silhouette.
    while len(mesh_obj.data.polygons) > t:
        current = len(mesh_obj.data.polygons)
        need = current - t
        angle = min(math.radians(30), math.radians(5) + need / current * math.radians(25))
        mod = mesh_obj.modifiers.new("Decimate", "DECIMATE")
        mod.decimate_type = "COLLAPSE"
        mod.ratio = max(0.01, t / current)
        mod.use_symmetry = False
        mod.use_collapse_triangulate = True
        bpy.ops.object.modifier_apply(modifier=mod.name)
        if len(mesh_obj.data.polygons) == current:
            break  # No progress — stop

    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.normals_make_consistent()
    bpy.ops.object.mode_set(mode="OBJECT")

    return len(mesh_obj.data.polygons)


# ---------------------------------------------------------------------------
# Public API — path-based (GLB → GLB)
# ---------------------------------------------------------------------------


def prepare_mesh_topology(
    input_path: Path | str, output_path: Path | str | None = None, **kwargs: object
) -> Path:
    """Prepara topologia de um GLB: remove doubles, weld, normais, fill holes.

    Carrega o GLB via bpy, aplica o pipeline de reparo e exporta. Preserva
    armatures, skins e animações quando presentes. Se bpy não estiver
    disponível, devolve o input inalterado com um warning.

    Args:
        input_path: GLB de entrada (ou objeto trimesh — backward compat).
        output_path: GLB de saída (se None, sobrepõe o ficheiro de entrada).

    Returns:
        Path para o GLB preparado (ou trimesh.Trimesh se input for trimesh).
    """
    _was_trimesh = hasattr(input_path, "export")
    try:
        return _prepare_mesh_topology_impl(input_path, output_path, _was_trimesh)
    except ImportError:
        logging.getLogger(__name__).warning(
            "bpy indisponível — prepare_mesh_topology ignorado (mesh NÃO foi reparada)"
        )
        if _was_trimesh:
            return input_path
        return Path(input_path)


def _prepare_mesh_topology_impl(input_path, output_path, _was_trimesh):
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
    _prepare_topology_bpy(mesh_obj)
    _export_glb(_output, mesh_obj, arm_objs)

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
            input_path, output_dir, basename, lod1_ratio, lod2_ratio,
            min_faces_lod1, min_faces_lod2, meshfix,
        )
    except ImportError:
        logging.getLogger(__name__).warning("bpy indisponível — generate_lod_glb_triplet ignorado")
        return []


def _generate_lod_glb_triplet_impl(
    input_path, output_dir, basename, lod1_ratio, lod2_ratio,
    min_faces_lod1, min_faces_lod2, meshfix,
):
    mesh_obj, arm_objs = _load_glb_with_armatures(input_path)
    n = len(mesh_obj.data.polygons)
    if n < 4:
        raise ValueError(f"Mesh com poucas faces ({n}); LOD não aplicável.")
    if meshfix:
        _fill_holes_bpy(mesh_obj, sides=30)
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
) -> list[Path]:
    """Gera três GLB texturizados por decimação com preservação de UV.

    Se ``target_faces`` for dado: LOD0 = target_faces, LOD1 = target/2, LOD2 = target/4.
    Caso contrário usa ``lod1_ratio`` e ``lod2_ratio`` sobre o original.
    """
    from text3d.utils.mesh_remesh_textured import remesh_textured_glb

    painted = Path(painted_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    import bpy
    from gamedev_shared.bpy_mesh import clear_scene

    clear_scene()
    bpy.ops.import_scene.gltf(filepath=str(painted))
    mesh_objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    n = len(max(mesh_objs, key=lambda o: len(o.data.polygons)).data.polygons)
    clear_scene()

    if target_faces and target_faces > 0:
        lod0_target = target_faces
        lod1_target = max(100, target_faces // 2)
        lod2_target = max(50, target_faces // 4)
    else:
        lod0_target = n
        lod1_target = max(min_faces_lod1, int(n * lod1_ratio))
        lod2_target = max(min_faces_lod2, int(n * lod2_ratio))

    out_paths: list[Path] = []

    lod0_path = output_dir / f"{basename}_lod0.glb"
    if lod0_target < n:
        remesh_textured_glb(painted, lod0_path, target_faces=lod0_target, texture_size=texture_size_lod0)
    else:
        import shutil
        shutil.copy2(painted, lod0_path)
    out_paths.append(lod0_path)

    for level, target, tex_size in (
        (1, lod1_target, max(256, texture_size_lod0 // 2)),
        (2, lod2_target, max(128, texture_size_lod0 // 4)),
    ):
        path = output_dir / f"{basename}_lod{level}.glb"
        remesh_textured_glb(painted, path, target_faces=target, texture_size=tex_size)
        out_paths.append(path)

    return out_paths
